# 实施方案 3.6 — 设备能力自发现（机器狗硬件接入）

> 参考来源：xiaozhi-esp32-server IOT 设备管理（`iotHandle.py`，分析报告 §3.6）
> 影响文件：`agents_v2/shared/device_registry.py`（新建）、`agents_v2/generic/main.py`

---

## 背景与问题

xiaozhi 的做法：硬件上报 JSON 能力描述符，服务端自动生成 function call schema，LLM 直接调用，无需为每个设备功能手写工具。

机器狗将来通过 WebSocket 或 MQTT 上报能力（电机、摄像头、陀螺仪等），需要一套**能力自发现**框架，实现"描述符 → LangChain tool → LLM 可调用"的全自动流程。

**当前阶段目标**：设计框架 + mock 测试，不需要真实 WebSocket 接入。

---

## 目标

1. `DeviceCapabilityRegistry` 接收 JSON 能力描述符，动态生成 LangChain tool
2. 生成的工具带 `ToolType.ACTION` / `QUERY` 分类，注册到全局 `TOOL_META`
3. `inject_to_agent()` 方法将设备工具合并到员工的工具列表
4. mock 测试覆盖：生成工具、调用、schema 验证
5. 预留 WebSocket/MQTT 接入 stub

---

## 设计

### 设备能力描述符格式

```json
{
  "device_id": "robot_dog_01",
  "device_type": "robot_dog",
  "methods": {
    "set_gait": {
      "description": "设置步态",
      "params": {"gait": {"type": "string", "enum": ["walk", "trot", "gallop", "stand"]}},
      "tool_type": "action"
    },
    "get_status": {
      "description": "查询当前状态（步态、速度、电量）",
      "params": {},
      "tool_type": "query"
    },
    "emergency_stop": {
      "description": "紧急停止所有运动",
      "params": {},
      "tool_type": "action"
    }
  }
}
```

### 核心类

```
DeviceCapabilityRegistry
  ├── connect_handler(handler)   # 注入通信处理器
  ├── register(descriptor)       # 注册设备 → 生成工具 → 返回工具名列表
  ├── unregister(device_id)      # 设备下线，移除工具
  ├── get_tools(device_id)       # 获取某设备的 tool 实例列表
  └── all_tools()                # 所有设备工具

DeviceHandler（抽象基类）
  ├── MockDeviceHandler          # mock，用于开发测试
  ├── WebSocketDeviceHandler     # stub，待实现
  └── MQTTDeviceHandler          # stub，待实现
```

---

## 实现方案

### 新建 `agents_v2/shared/device_registry.py`

```python
"""
设备能力自发现框架 — 接收设备 JSON 能力描述符，动态生成 LangChain tool。

用法示例：
    from agents_v2.shared.device_registry import device_registry, MockDeviceHandler
    device_registry.connect_handler(MockDeviceHandler())
    tool_names = device_registry.register(descriptor)
    tools = device_registry.all_tools()
"""
from __future__ import annotations
import json, logging
from abc import ABC, abstractmethod
from typing import Any
from langchain_core.tools import StructuredTool
from pydantic import create_model, Field
from agents_v2.shared.tools import TOOL_META, ToolMeta, ToolType

log = logging.getLogger(__name__)

_TYPE_MAP = {"string": str, "number": float, "integer": int, "boolean": bool}


class DeviceHandler(ABC):
    @abstractmethod
    def call_method(self, device_id: str, method: str, params: dict) -> Any: ...


class MockDeviceHandler(DeviceHandler):
    def call_method(self, device_id: str, method: str, params: dict) -> Any:
        if method == "get_status":
            return json.dumps({"device_id": device_id, "gait": "stand",
                               "speed": 0.0, "battery": 85}, ensure_ascii=False)
        if method == "emergency_stop":
            return f"[{device_id}] 紧急停止已执行"
        return f"[{device_id}] {method}({params}) 执行成功（mock）"


class WebSocketDeviceHandler(DeviceHandler):
    """stub — 待机器狗 WebSocket 接入时实现。"""
    def call_method(self, device_id: str, method: str, params: dict) -> Any:
        raise NotImplementedError("WebSocketDeviceHandler not yet implemented")


class MQTTDeviceHandler(DeviceHandler):
    """stub — 待 MQTT broker 接入时实现。"""
    def call_method(self, device_id: str, method: str, params: dict) -> Any:
        raise NotImplementedError("MQTTDeviceHandler not yet implemented")


def _tool_name(device_id: str, method: str) -> str:
    safe = device_id.replace("-", "_").replace(".", "_")
    return f"{safe}__{method}"


def _build_args_model(method_name: str, params: dict):
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


class DeviceCapabilityRegistry:
    def __init__(self):
        self._handler: DeviceHandler = MockDeviceHandler()
        self._device_tools: dict[str, list[str]] = {}

    def connect_handler(self, handler: DeviceHandler) -> None:
        self._handler = handler

    def register(self, descriptor: dict) -> list[str]:
        device_id = descriptor.get("device_id", "unknown")
        methods = descriptor.get("methods", {})
        generated: list[str] = []

        for method_name, spec in methods.items():
            tname = _tool_name(device_id, method_name)
            description = spec.get("description", method_name)
            params = spec.get("params", {})
            tool_type = ToolType.ACTION if spec.get("tool_type", "action") == "action" else ToolType.QUERY
            args_model = _build_args_model(method_name, params)
            d, m, h = device_id, method_name, self._handler

            if args_model:
                def _make(d, m, h, model):
                    def fn(**kwargs) -> str:
                        try: return str(h.call_method(d, m, kwargs))
                        except Exception as e: return f"设备调用失败 [{d}.{m}]: {e}"
                    fn.__name__ = _tool_name(d, m)
                    return fn
                lc_tool = StructuredTool.from_function(
                    func=_make(d, m, h, args_model),
                    name=tname,
                    description=f"[{device_id}] {description}",
                    args_schema=args_model,
                )
            else:
                def _make_np(d, m, h):
                    def fn() -> str:
                        try: return str(h.call_method(d, m, {}))
                        except Exception as e: return f"设备调用失败 [{d}.{m}]: {e}"
                    fn.__name__ = _tool_name(d, m)
                    return fn
                lc_tool = StructuredTool.from_function(
                    func=_make_np(d, m, h),
                    name=tname,
                    description=f"[{device_id}] {description}",
                )

            TOOL_META[tname] = ToolMeta(tool=lc_tool, type=tool_type,
                                         hint=f"{device_id} - {description}",
                                         auto_register=False)
            generated.append(tname)
            log.info("DeviceRegistry: registered %s (%s)", tname, tool_type.value)

        self._device_tools[device_id] = generated
        return generated

    def unregister(self, device_id: str) -> None:
        for tname in self._device_tools.pop(device_id, []):
            TOOL_META.pop(tname, None)
        log.info("DeviceRegistry: unregistered %s", device_id)

    def get_tools(self, device_id: str) -> list:
        return [TOOL_META[n].tool for n in self._device_tools.get(device_id, []) if n in TOOL_META]

    def all_tools(self) -> list:
        tools = []
        for did in self._device_tools:
            tools.extend(self.get_tools(did))
        return tools

    def list_devices(self) -> list[dict]:
        return [{"device_id": did, "tools": names}
                for did, names in self._device_tools.items()]


# 进程级单例
device_registry = DeviceCapabilityRegistry()
```

### `agents_v2/generic/main.py` 接入（可选，通过 behavior 配置）

```python
# lifespan() 内，tools 构建完成后

from agents_v2.shared.device_registry import device_registry
device_caps = (cfg.behavior or {}).get("device_capabilities", [])
for cap in device_caps:
    if isinstance(cap, dict) and cap.get("methods"):
        device_registry.register(cap)

device_tools = device_registry.all_tools()
if device_tools:
    tools = (tools or []) + device_tools
```

---

## 改动文件汇总

| 文件 | 改动类型 | 估计行数 |
|------|---------|---------|
| `agents_v2/shared/device_registry.py` | **新建** | ~130 行 |
| `agents_v2/generic/main.py` | lifespan 增加设备工具注入（可选） | +8 行 |

---

## 验证方法

```python
def test_register_generates_tools():
    from agents_v2.shared.device_registry import DeviceCapabilityRegistry
    reg = DeviceCapabilityRegistry()
    names = reg.register({
        "device_id": "test_dog_01",
        "methods": {
            "set_gait": {"description": "设置步态", "params": {"gait": {"type": "string"}}, "tool_type": "action"},
            "get_status": {"description": "查询状态", "params": {}, "tool_type": "query"},
        }
    })
    assert "test_dog_01__set_gait" in names
    assert "test_dog_01__get_status" in names

def test_tool_type_mapping():
    from agents_v2.shared.device_registry import DeviceCapabilityRegistry
    from agents_v2.shared.tools import TOOL_META, ToolType
    reg = DeviceCapabilityRegistry()
    reg.register({"device_id": "d1", "methods": {
        "act": {"description": "x", "params": {}, "tool_type": "action"},
        "qry": {"description": "y", "params": {}, "tool_type": "query"},
    }})
    assert TOOL_META["d1__act"].type == ToolType.ACTION
    assert TOOL_META["d1__qry"].type == ToolType.QUERY

def test_unregister():
    from agents_v2.shared.device_registry import DeviceCapabilityRegistry
    from agents_v2.shared.tools import TOOL_META
    reg = DeviceCapabilityRegistry()
    reg.register({"device_id": "d2", "methods": {"stop": {"description": "s", "params": {}, "tool_type": "action"}}})
    reg.unregister("d2")
    assert "d2__stop" not in TOOL_META
```

---

## 风险点

| 风险 | 缓解 |
|------|------|
| 同名设备重复注册（工具名冲突） | `register()` 前先 `unregister()` 同 device_id |
| LLM 幻觉参数导致设备调用失败 | 工具内有异常捕获，返回明确错误信息，不 crash |
| 设备下线但工具未 unregister | WebSocket 断开时触发 `unregister()`；LLM 调用失效工具时返回友好错误 |
| `TOOL_META` 全局 dict 并发写 | 每个 agent 是独立进程，无跨进程共享，无需加锁 |
