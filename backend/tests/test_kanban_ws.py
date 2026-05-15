"""
KanbanAdapter + WebSocket 端点测试 — K-A1 ~ K-A8

因 KanbanAdapter 在 lifespan 内启动，这里通过 mock bus_pool 隔离 Redis 依赖。
数据库使用 in-memory SQLite，过滤掉不兼容的 pgvector/JSONB 表。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.db import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
_SKIP_TABLES = {"employee_memory"}  # pgvector


def _compat_tables():
    return [t for n, t in Base.metadata.tables.items() if n not in _SKIP_TABLES]


async def _make_session_factory():
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    tables = _compat_tables()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# ── K-A1: connect / disconnect ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kanban_adapter_connect_registers_ws():
    """K-A1: connect() 接受 WebSocket 并注册到连接池"""
    from backend.chat.kanban_adapter import KanbanAdapter

    adapter = KanbanAdapter()
    mock_ws = AsyncMock()
    await adapter.connect("test_channel", mock_ws)

    mock_ws.accept.assert_called_once()
    assert mock_ws in adapter._connections["test_channel"]


@pytest.mark.asyncio
async def test_kanban_adapter_disconnect_removes_ws():
    """K-A1b: disconnect() 从连接池中移除 WebSocket"""
    from backend.chat.kanban_adapter import KanbanAdapter

    adapter = KanbanAdapter()
    mock_ws = AsyncMock()
    await adapter.connect("test_channel", mock_ws)

    adapter.disconnect("test_channel", mock_ws)
    assert mock_ws not in adapter._connections.get("test_channel", [])


# ── K-A2: disconnect 对不存在的 ws 幂等 ──────────────────────────────────────

def test_kanban_adapter_disconnect_unknown_ws_is_noop():
    """K-A2: disconnect 不存在的 ws 不抛异常"""
    from backend.chat.kanban_adapter import KanbanAdapter

    adapter = KanbanAdapter()
    adapter.disconnect("nonexistent", MagicMock())  # 不应抛异常


# ── K-A3: on_message 持久化 user 消息并 publish 到 Redis ─────────────────────

@pytest.mark.asyncio
async def test_kanban_on_message_persists_and_publishes():
    """K-A3: on_message() 写入 ChatMessage 并调用 bus_pool.pub_bus.publish_message"""
    from backend.chat.kanban_adapter import KanbanAdapter

    engine, SessionFactory = await _make_session_factory()
    adapter = KanbanAdapter()
    mock_bus_pool = MagicMock()
    mock_bus_pool.pub_bus.publish_message = AsyncMock()
    adapter._bus_pool = mock_bus_pool

    with patch("backend.core.db.AsyncSessionLocal", SessionFactory):
        await adapter.on_message("kanban_group", "CEO", "大家好")

    mock_bus_pool.pub_bus.publish_message.assert_called_once()
    event = mock_bus_pool.pub_bus.publish_message.call_args[0][0]
    assert event.sender == "CEO"
    assert event.text == "大家好"
    assert event.chat_id == "kanban_group"

    await engine.dispose()


# ── K-A4: on_message bus_pool=None 时不崩溃 ──────────────────────────────────

@pytest.mark.asyncio
async def test_kanban_on_message_no_bus_pool_graceful():
    """K-A4: bus_pool 为 None 时 on_message 不抛异常（仅持久化，不 publish）"""
    from backend.chat.kanban_adapter import KanbanAdapter

    engine, SessionFactory = await _make_session_factory()
    adapter = KanbanAdapter()  # bus_pool 未设置 (None)

    with patch("backend.core.db.AsyncSessionLocal", SessionFactory):
        await adapter.on_message("kanban_group", "CEO", "测试消息")  # 不应抛异常

    await engine.dispose()


# ── K-A5: _handle_req 推送 WS 消息 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_kanban_handle_req_pushes_to_connected_ws():
    """K-A5: _handle_req() 调用 LLM 成功后，向所有连接的 WS 发送 JSON"""
    from backend.chat.kanban_adapter import KanbanAdapter
    from group_chat.models import SpeakRequest

    engine, SessionFactory = await _make_session_factory()
    adapter = KanbanAdapter()
    mock_bus_pool = MagicMock()
    mock_bus_pool.pub_bus.publish_speak_resp = AsyncMock()
    adapter._bus_pool = mock_bus_pool

    mock_ws = AsyncMock()
    adapter._connections["kanban_group"] = [mock_ws]

    req = SpeakRequest(
        session_id="sess-001",
        chat_id="kanban_group",
        employee="algorithm",
        history_text="用户：你好\n",
        trigger_message_id="msg-001",
    )

    with patch("backend.core.db.AsyncSessionLocal", SessionFactory), \
         patch("backend.chat.kanban_adapter._invoke_agent",
               AsyncMock(return_value="你好，我是算法工程师")):
        await adapter._handle_req(req)

    mock_ws.send_json.assert_called_once()
    sent = mock_ws.send_json.call_args[0][0]
    assert sent["type"] == "message"
    assert sent["role"] == "assistant"
    assert sent["sender"] == "algorithm"
    assert "算法工程师" in sent["content"]

    mock_bus_pool.pub_bus.publish_speak_resp.assert_called_once()
    resp = mock_bus_pool.pub_bus.publish_speak_resp.call_args[0][0]
    assert resp.success is True
    assert resp.employee == "algorithm"

    await engine.dispose()


# ── K-A6: _handle_req agent 调用失败时 success=False ─────────────────────────

@pytest.mark.asyncio
async def test_kanban_handle_req_agent_fail_publishes_failure():
    """K-A6: _invoke_agent 返回 None 时，SpeakResponse.success=False"""
    from backend.chat.kanban_adapter import KanbanAdapter
    from group_chat.models import SpeakRequest

    engine, SessionFactory = await _make_session_factory()
    adapter = KanbanAdapter()
    mock_bus_pool = MagicMock()
    mock_bus_pool.pub_bus.publish_speak_resp = AsyncMock()
    adapter._bus_pool = mock_bus_pool

    req = SpeakRequest(
        session_id="sess-002",
        chat_id="kanban_group",
        employee="mechanical",
        history_text="",
        trigger_message_id="",
    )

    with patch("backend.core.db.AsyncSessionLocal", SessionFactory), \
         patch("backend.chat.kanban_adapter._invoke_agent", AsyncMock(return_value=None)):
        await adapter._handle_req(req)

    resp = mock_bus_pool.pub_bus.publish_speak_resp.call_args[0][0]
    assert resp.success is False

    await engine.dispose()


# ── K-A7: stop 清空连接并取消任务 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kanban_adapter_stop_clears_connections():
    """K-A7: stop() 清空 _connections，取消 _listen_task"""
    from backend.chat.kanban_adapter import KanbanAdapter

    adapter = KanbanAdapter()
    mock_ws = AsyncMock()
    await adapter.connect("ch1", mock_ws)

    async def _noop():
        await asyncio.sleep(9999)

    adapter._listen_task = asyncio.create_task(_noop())
    await adapter.stop()
    # cancel() 是非阻塞的，让事件循环处理取消
    await asyncio.sleep(0)

    assert adapter._connections == {}
    # task 应处于 cancelling/cancelled/done 状态（cancel 已调用）
    task = adapter._listen_task
    assert task.cancelled() or task.done() or task.cancelling() > 0


# ── K-A8: WebSocket 路由存在 ─────────────────────────────────────────────────

def test_ws_chat_endpoint_registered():
    """K-A8: /api/ws/chat/{channel_id} WebSocket 路由已注册在 app.routes 中"""
    from backend.main import app
    from starlette.routing import WebSocketRoute

    ws_paths = [
        getattr(r, "path", "")
        for r in app.routes
        if isinstance(r, WebSocketRoute)
    ]
    # 也可能被封装在子路由器里，需要展开
    if not ws_paths:
        from starlette.routing import Mount

        for r in app.routes:
            if isinstance(r, Mount):
                ws_paths += [
                    getattr(sub, "path", "")
                    for sub in getattr(r, "routes", [])
                    if isinstance(sub, WebSocketRoute)
                ]

    assert any("/chat/" in p or "{channel_id}" in p for p in ws_paths), \
        f"WebSocket /chat/{{channel_id}} not found in routes: {ws_paths}"
