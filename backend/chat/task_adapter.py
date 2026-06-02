"""任务平台适配器(B1.3)。

订阅 speak_req:*:task:* 频道,把 orchestrator 下发的 speak_req 通过 A2A
转给员工 agent,再 publish speak_resp 回 orchestrator。

与 KanbanAdapter 的差异:
- chat_id 是 "task:{task_id}",不是 "kanban_*"
- 不维护 WebSocket 连接(产物落到员工 cwd 文件系统)
- 把员工回复落库到 task_step.output (可选,B2 阶段做)

随 backend lifespan 启动。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from group_chat.platform import PlatformAdapter

if TYPE_CHECKING:
    from group_chat.event_bus import GroupEventBusPool
    from group_chat.models import SpeakRequest

log = logging.getLogger(__name__)

TASK_PREFIX = "task:"
_AGENT_TIMEOUT = 600.0  # 10 分钟,留给 claude CLI 跑工程任务


class TaskAdapter(PlatformAdapter):
    """task: 频道适配器:把 speak_req 桥接到员工 A2A 入口。"""

    platform_id = "task"

    def __init__(self) -> None:
        self._bus_pool: "GroupEventBusPool | None" = None
        self._listen_task: asyncio.Task | None = None

    async def start(self, bus_pool: "GroupEventBusPool") -> None:
        self._bus_pool = bus_pool
        self._listen_task = asyncio.create_task(self._listen_speak_req())
        log.info("TaskAdapter: started (subscribe speak_req:*:%s*)", TASK_PREFIX)

    async def stop(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
        log.info("TaskAdapter: stopped")

    async def _listen_speak_req(self) -> None:
        assert self._bus_pool is not None
        await self._bus_pool.sub_bus.subscribe_speak_req_pattern(
            f"{TASK_PREFIX}*",
            callback=lambda req: asyncio.ensure_future(self._handle_req(req)),
        )

    async def _handle_req(self, req: "SpeakRequest") -> None:
        from group_chat.models import SpeakResponse

        log.info("TaskAdapter: handling speak_req employee=%s chat=%s",
                 req.employee, req.chat_id)
        content = await _invoke_agent(req.employee, req.history_text, req.role_context)

        if self._bus_pool:
            await self._bus_pool.pub_bus.publish_speak_resp(SpeakResponse(
                session_id=req.session_id,
                chat_id=req.chat_id,
                employee=req.employee,
                content=content or "",
                success=bool(content),
            ))


async def _invoke_agent(
    employee: str,
    history_text: str,
    role_context: str = "",
) -> str | None:
    """通过 A2A 调员工 agent,返回回复内容。

    把 history_text + role_context 拼起来作为 prompt 传给员工。
    """
    from backend.services import registry

    cfg = registry.get_effective_sync(employee)
    if not cfg:
        log.warning("TaskAdapter: employee=%s not found", employee)
        return None

    prompt_parts = []
    if role_context:
        prompt_parts.append(f"[场景上下文]\n{role_context}")
    if history_text:
        prompt_parts.append(f"[历史对话]\n{history_text}")
    prompt = "\n\n".join(prompt_parts) or history_text

    try:
        from feishu.cc_req_client import ask_employee
        return await ask_employee(employee, prompt, timeout=_AGENT_TIMEOUT)
    except Exception as exc:
        log.warning("TaskAdapter: agent call failed employee=%s err=%s", employee, exc)
        return None


task_adapter = TaskAdapter()
