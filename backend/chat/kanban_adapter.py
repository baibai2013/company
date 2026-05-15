"""看板聊天平台适配器。

进：浏览器 WebSocket 消息 → 持久化到 PostgreSQL → Redis publish group_msg:{channel_id}
出：Redis speak_req:{employee}:{channel_id} → 调用 LLM → 持久化 → 推送 WS → publish speak_resp
"""
from __future__ import annotations

import asyncio
import logging
from uuid import uuid4
from typing import TYPE_CHECKING

from group_chat.platform import PlatformAdapter

if TYPE_CHECKING:
    from fastapi import WebSocket
    from group_chat.event_bus import GroupEventBusPool
    from group_chat.models import SpeakRequest

log = logging.getLogger(__name__)

# 看板频道前缀，用于 pattern subscribe 区分平台
KANBAN_PREFIX = "kanban_"
GROUP_CHANNEL = f"{KANBAN_PREFIX}group"


class KanbanAdapter(PlatformAdapter):
    """看板聊天适配器。管理 WebSocket 连接池，双向桥接 group_chat 引擎。"""

    platform_id = "kanban"

    def __init__(self) -> None:
        # channel_id → 活跃 WebSocket 连接列表
        self._connections: dict[str, list["WebSocket"]] = {}
        self._bus_pool: GroupEventBusPool | None = None
        self._listen_task: asyncio.Task | None = None

    # ── PlatformAdapter 接口 ─────────────────────────────────────────────────

    async def start(self, bus_pool: "GroupEventBusPool") -> None:
        self._bus_pool = bus_pool
        self._listen_task = asyncio.create_task(self._listen_speak_req())
        log.info("KanbanAdapter: started")

    async def stop(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
        self._connections.clear()
        log.info("KanbanAdapter: stopped")

    # ── WebSocket 连接管理 ───────────────────────────────────────────────────

    async def connect(self, channel_id: str, ws: "WebSocket") -> None:
        """接受新的 WebSocket 连接并注册。"""
        await ws.accept()
        self._connections.setdefault(channel_id, []).append(ws)
        log.info("KanbanAdapter: ws connected channel=%s total=%d",
                 channel_id, len(self._connections[channel_id]))

    def disconnect(self, channel_id: str, ws: "WebSocket") -> None:
        """移除断开的 WebSocket 连接。"""
        conns = self._connections.get(channel_id, [])
        if ws in conns:
            conns.remove(ws)
        log.info("KanbanAdapter: ws disconnected channel=%s remaining=%d",
                 channel_id, len(conns))

    # ── 消息流入（WS → Redis）────────────────────────────────────────────────

    async def on_message(self, channel_id: str, sender: str, text: str) -> None:
        """客户端通过 WS 发送消息时调用：持久化 user 消息并注入 group_chat 引擎。"""
        from backend.core.db import AsyncSessionLocal
        from backend.models.message import ChatMessage
        from group_chat.models import MessageEvent

        async with AsyncSessionLocal() as s:
            s.add(ChatMessage(
                channel=channel_id,
                role="user",
                sender=sender,
                content=text,
            ))
            await s.commit()

        if self._bus_pool:
            await self._bus_pool.pub_bus.publish_message(MessageEvent(
                message_id=str(uuid4()),
                chat_id=channel_id,
                sender=sender,
                text=text,
            ))

    # ── 消息流出（Redis speak_req → LLM → WS）───────────────────────────────

    async def _listen_speak_req(self) -> None:
        """订阅 orchestrator 下发的所有 kanban_* 频道的 speak_req。"""
        assert self._bus_pool is not None
        await self._bus_pool.sub_bus.subscribe_speak_req_pattern(
            f"{KANBAN_PREFIX}*",
            callback=lambda req: asyncio.ensure_future(self._handle_req(req)),
        )

    async def _handle_req(self, req: "SpeakRequest") -> None:
        """处理一条 speak_req：调用 LLM，持久化，推送 WS，通知 orchestrator。"""
        from backend.core.db import AsyncSessionLocal
        from backend.models.message import ChatMessage
        from group_chat.models import SpeakResponse

        content = await _invoke_agent(req.employee, req.history_text)

        # 持久化 assistant 消息
        async with AsyncSessionLocal() as s:
            s.add(ChatMessage(
                channel=req.chat_id,
                role="assistant",
                sender=req.employee,
                content=content or "",
            ))
            await s.commit()

        # 推送给所有在线 WS 客户端
        msg = {
            "type": "message",
            "role": "assistant",
            "sender": req.employee,
            "content": content or "",
        }
        for ws in list(self._connections.get(req.chat_id, [])):
            try:
                await ws.send_json(msg)
            except Exception:
                self.disconnect(req.chat_id, ws)

        # 通知 orchestrator 发言完成
        if self._bus_pool:
            await self._bus_pool.pub_bus.publish_speak_resp(SpeakResponse(
                session_id=req.session_id,
                chat_id=req.chat_id,
                employee=req.employee,
                content=content or "",
                success=bool(content),
            ))


async def _invoke_agent(employee: str, history_text: str) -> str | None:
    """通过 A2A 协议调用员工 agent，返回回复内容。"""
    import importlib
    from backend.services import registry

    cfg = registry.get_effective_sync(employee)
    if not cfg or not cfg.agent_port:
        return None
    try:
        a2a = importlib.import_module("agents_v2.shared.a2a_server")
        return await a2a.call_agent(f"http://localhost:{cfg.agent_port}/", history_text, timeout=120)
    except Exception as exc:
        log.warning("KanbanAdapter: agent call failed employee=%s err=%s", employee, exc)
        return None


# 单例，供 ws.py 和 main.py 共用
kanban_adapter = KanbanAdapter()
