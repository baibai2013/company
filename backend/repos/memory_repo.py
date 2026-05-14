"""员工长期记忆仓库。

提供异步写入（供 orchestrator 在会话结束后调用）和同步读取（供 smart_graph 注入 system prompt）。

读取走内存缓存（启动时预热，写入后刷新），热路径零 DB 开销。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from backend.core.db import AsyncSessionLocal
from backend.models.memory import EmployeeMemory

if TYPE_CHECKING:
    from feishu.group_chat.models import GroupSession

log = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────────────────
# key → list[str] (最近 N 条 content，按时间倒序)
_cache: dict[str, list[str]] = {}
_CACHE_SIZE = 10   # 每人最多缓存条数
_cache_warmed = False


async def warmup() -> None:
    """进程启动时预热全员记忆缓存（由 registry.warmup_sync 的 asyncio.run 触发）。"""
    global _cache_warmed
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(EmployeeMemory)
            .order_by(EmployeeMemory.created_at.desc())
            .limit(500)
        )).scalars().all()

    fresh: dict[str, list[str]] = {}
    for row in rows:
        bucket = fresh.setdefault(row.employee_key, [])
        if len(bucket) < _CACHE_SIZE:
            bucket.append(row.content)

    _cache.clear()
    _cache.update(fresh)
    _cache_warmed = True
    log.info("memory_repo: warmed cache for %d employees", len(_cache))


def warmup_sync() -> None:
    """同步预热入口，供无事件循环的进程启动阶段调用（如 smart_graph 初始化）。"""
    global _cache_warmed
    if _cache_warmed:
        return
    try:
        asyncio.run(warmup())
    except RuntimeError:
        pass  # 在事件循环内调用：等异步写入触发刷新


def get_sync(employee_key: str) -> list[str]:
    """从缓存读取记忆（同步，适合 smart_graph 节点）。"""
    if not _cache_warmed:
        warmup_sync()
    return _cache.get(employee_key, [])


# ── Write ─────────────────────────────────────────────────────────────────────

async def save(
    employee_key: str,
    content: str,
    session_id: str = "",
    chat_id: str = "",
    template: str = "free",
) -> None:
    """写入一条记忆，并刷新该员工的缓存。"""
    async with AsyncSessionLocal() as s:
        s.add(EmployeeMemory(
            employee_key=employee_key,
            content=content,
            session_id=session_id or None,
            chat_id=chat_id or None,
            template=template or None,
        ))
        await s.commit()

    # 刷新缓存
    bucket = _cache.setdefault(employee_key, [])
    bucket.insert(0, content)
    if len(bucket) > _CACHE_SIZE:
        bucket.pop()


async def save_session_summary(session: "GroupSession") -> None:
    """会话结束后，将摘要写入所有参与者的记忆。

    仅当 session.summary 非空时执行。每个员工各存一行（内容相同），
    便于按 employee_key 独立检索。
    """
    summary = getattr(session, "summary", "") or ""
    if not summary:
        return

    # 取真实员工（排除 "user" / "user:xxx"）
    employees = [p for p in session.participants if not p.startswith("user")]
    if not employees:
        return

    template = getattr(session, "template", "free") or "free"
    log.info("memory_repo: saving session summary for %d employees session=%s",
             len(employees), session.id[:8])

    for emp in employees:
        await save(emp, summary,
                   session_id=session.id,
                   chat_id=session.chat_id,
                   template=template)


# ── Read (async, for admin/debug) ─────────────────────────────────────────────

async def get_recent(employee_key: str, limit: int = 10) -> list[EmployeeMemory]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(EmployeeMemory)
            .where(EmployeeMemory.employee_key == employee_key)
            .order_by(EmployeeMemory.created_at.desc())
            .limit(limit)
        )).scalars().all()
    return list(rows)
