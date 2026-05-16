"""
设备能力自发现框架 — 接收设备 JSON 能力描述符，动态生成 LangChain tool。

使用示例：
    from agents_v2.shared.device_registry import device_registry, MockDeviceHandler

    device_registry.connect_handler(MockDeviceHandler())
    tool_names = device_registry.register({
        "device_id": "robot_dog_01",
        "methods": {
            "set_gait": {
                "description": "设置步态",
                "params": {"gait": {"type": "string", "enum": ["walk", "trot"]}},
                "tool_type": "action",
            },
        },
    })
    tools = device_registry.all_tools()
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import create_model, Field

from agents_v2.shared.tools import TOOL_META, ToolMeta, ToolType

log = logging.getLogger(__name__)

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}


# ── 设备通信处理器 ────────────────────────────────────────────────────────────

class DeviceHandler(ABC):
    """向设备发送命令并获取响应的抽象基类。"""

    @abstractmethod
    def call_method(self, device_id: str, method: str, params: dict) -> Any:
        """同步执行设备方法，返回结果字符串或 dict。"""
        ...


class MockDeviceHandler(DeviceHandler):
    """Mock 处理器 — 返回模拟数据，用于开发和测试。"""

    def call_method(self, device_id: str, method: str, params: dict) -> Any:
        if method == "get_status":
            return json.dumps({
                "device_id": device_id,
                "gait": "stand",
                "speed": 0.0,
                "battery": 85,
                "pose": "upright",
            }, ensure_ascii=False)
        if method == "emergency_stop":
            return f"[{device_id}] 紧急停止已执行"
        return f"[{device_id}] {method}({params}) 执行成功（mock）"


class WebSocketDeviceHandler(DeviceHandler):
    """WebSocket 通信处理器 — stub，待机器狗接入时实现。"""

    def __init__(self, ws_url: str = "ws://robot-dog:8765"):
        self.ws_url = ws_url

    def call_method(self, device_id: str, method: str, params: dict) -> Any:
        raise NotImplementedError(
            f"WebSocketDeviceHandler 尚未实现。"
            f"尝试调用 {device_id}.{method}({params})"
        )


class MQTTDeviceHandler(DeviceHandler):
    """MQTT 通信处理器 — stub，待 MQTT broker 接入时实现。"""

    def call_method(self, device_id: str, method: str, params: dict) -> Any:
        raise NotImplementedError("MQTTDeviceHandler 尚未实现。")


# ── 内部工具函数 ──────────────────────────────────────────────────────────────

def _tool_name(device_id: str, method: str) -> str:
    """device_id + method → LangChain 兼容的工具名（仅含字母数字下划线）。"""
    safe = device_id.replace("-", "_").replace(".", "_")
    return f"{safe}__{method}"


def _build_args_model(method_name: str, params: dict):
    """把方法参数 dict 转成 Pydantic 动态模型，供 StructuredTool 使用。无参数时返回 None。"""
    if not params:
        return None
    fields = {}
    for p_name, p_spec in params.items():
        py_type = _TYPE_MAP.get(p_spec.get("type", "string"), str)
        desc = p_spec.get("description", p_name)
        if "enum" in p_spec:
            desc += f"，可选值：{p_spec['enum']}"
        fields[p_name] = (py_type, Field(..., description=desc))
    return create_model(f"{method_name}_params", **fields)


# ── DeviceCapabilityRegistry ─────────────────────────────────────────────────

class DeviceCapabilityRegistry:
    """
    管理设备能力描述符，动态生成并注册 LangChain tool。

    生命周期：
        1. connect_handler(handler)  — 注入通信处理器（默认 MockDeviceHandler）
        2. register(descriptor)      — 注册设备，生成工具并加入全局 TOOL_META
        3. all_tools()               — 获取所有设备工具实例，传给 build_smart_agent
        4. unregister(device_id)     — 设备下线时移除工具
    """

    def __init__(self):
        self._handler: DeviceHandler = MockDeviceHandler()
        self._device_tools: dict[str, list[str]] = {}  # device_id → tool names

    def connect_handler(self, handler: DeviceHandler) -> None:
        """注入通信处理器（mock / WebSocket / MQTT）。"""
        self._handler = handler
        log.info("DeviceRegistry: handler set to %s", type(handler).__name__)

    def register(self, descriptor: dict) -> list[str]:
        """
        解析能力描述符，为每个 method 生成 LangChain tool，
        注册到全局 TOOL_META，返回生成的工具名列表。
        """
        device_id = descriptor.get("device_id", "unknown")
        methods = descriptor.get("methods", {})

        if not methods:
            log.warning("DeviceRegistry.register(%s): no methods in descriptor", device_id)
            return []

        # 同名设备重新注册时先清理旧工具
        if device_id in self._device_tools:
            self.unregister(device_id)

        generated: list[str] = []

        for method_name, spec in methods.items():
            tname = _tool_name(device_id, method_name)
            description = spec.get("description", method_name)
            params = spec.get("params", {})
            raw_type = spec.get("tool_type", "action").lower()
            tool_type = ToolType.ACTION if raw_type == "action" else ToolType.QUERY
            args_model = _build_args_model(method_name, params)

            d, m, h = device_id, method_name, self._handler

            if args_model:
                def _make_func(d, m, h, model):
                    def fn(**kwargs) -> str:
                        try:
                            return str(h.call_method(d, m, kwargs))
                        except Exception as e:
                            return f"设备调用失败 [{d}.{m}]: {e}"
                    fn.__name__ = _tool_name(d, m)
                    return fn
                lc_tool = StructuredTool.from_function(
                    func=_make_func(d, m, h, args_model),
                    name=tname,
                    description=f"[{device_id}] {description}",
                    args_schema=args_model,
                )
            else:
                def _make_no_param_func(d, m, h):
                    def fn() -> str:
                        try:
                            return str(h.call_method(d, m, {}))
                        except Exception as e:
                            return f"设备调用失败 [{d}.{m}]: {e}"
                    fn.__name__ = _tool_name(d, m)
                    return fn
                lc_tool = StructuredTool.from_function(
                    func=_make_no_param_func(d, m, h),
                    name=tname,
                    description=f"[{device_id}] {description}",
                )

            TOOL_META[tname] = ToolMeta(
                tool=lc_tool,
                type=tool_type,
                hint=f"{device_id} - {description}",
                auto_register=False,
            )
            generated.append(tname)
            log.info("DeviceRegistry: registered %s (%s)", tname, tool_type.value)

        self._device_tools[device_id] = generated
        return generated

    def unregister(self, device_id: str) -> None:
        """移除设备的所有工具（设备下线时调用）。"""
        for tname in self._device_tools.pop(device_id, []):
            TOOL_META.pop(tname, None)
        log.info("DeviceRegistry: unregistered %s", device_id)

    def get_tools(self, device_id: str) -> list:
        """获取某设备的 tool 实例列表。"""
        return [TOOL_META[n].tool for n in self._device_tools.get(device_id, []) if n in TOOL_META]

    def all_tools(self) -> list:
        """获取所有已注册设备的 tool 实例列表。"""
        tools = []
        for did in self._device_tools:
            tools.extend(self.get_tools(did))
        return tools

    def list_devices(self) -> list[dict]:
        """列出已注册设备及其工具摘要。"""
        return [
            {"device_id": did, "tools": names}
            for did, names in self._device_tools.items()
        ]


# ── 进程级单例 ────────────────────────────────────────────────────────────────
device_registry = DeviceCapabilityRegistry()
