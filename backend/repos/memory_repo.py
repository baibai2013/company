"""员工长期记忆仓库。

写入：异步，自动生成 embedding（需 OPENAI_API_KEY）。
读取：同步缓存（热路径）+ 异步语义检索（按 query 向量相似度排序）。

降级策略：
  - 无 OPENAI_API_KEY → 跳过 embedding，回退时间倒序
  - pgvector 不可用 → 同上
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select, text

from backend.core.db import AsyncSessionLocal
from backend.models.memory import EmployeeMemory

if TYPE_CHECKING:
    from group_chat.models import GroupSession

log = logging.getLogger(__name__)

_EMBED_DIM = 1536
_CACHE_SIZE = 10

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict[str, list[str]] = {}
_cache_warmed = False


async def warmup() -> None:
    """进程启动时预热全员记忆缓存。"""
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
    """同步预热入口，供无事件循环的进程启动阶段调用。"""
    global _cache_warmed
    if _cache_warmed:
        return
    coro = warmup()
    try:
        asyncio.run(coro)
    except RuntimeError:
        coro.close()  # 已在事件循环中，关闭协程避免 "never awaited" 警告


def get_sync(employee_key: str) -> list[str]:
    """从缓存读取最近记忆（同步，适合 smart_graph 节点）。"""
    if not _cache_warmed:
        warmup_sync()
    return _cache.get(employee_key, [])


# ── Embedding ─────────────────────────────────────────────────────────────────

async def _embed(text_: str) -> list[float] | None:
    """生成文本 embedding，失败或无 API Key 时返回 None。"""
    try:
        from backend.core.config import settings
        if not settings.OPENAI_API_KEY:
            return None
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL or None,
        )
        resp = await client.embeddings.create(
            model="text-embedding-3-small",
            input=text_,
        )
        return resp.data[0].embedding
    except Exception as e:
        log.debug("memory_repo: embedding failed: %s", e)
        return None


def _embed_sync(text_: str) -> list[float] | None:
    """同步版 embedding，供 smart_graph 节点调用（运行在 thread executor 中）。"""
    try:
        return asyncio.run(_embed(text_))
    except RuntimeError:
        return None


# ── Write ─────────────────────────────────────────────────────────────────────

async def save(
    employee_key: str,
    content: str,
    session_id: str = "",
    chat_id: str = "",
    template: str = "free",
) -> None:
    """写入一条记忆（自动生成 embedding），并刷新缓存。"""
    embedding = await _embed(content)

    async with AsyncSessionLocal() as s:
        s.add(EmployeeMemory(
            employee_key=employee_key,
            content=content,
            embedding=embedding,
            session_id=session_id or None,
            chat_id=chat_id or None,
            template=template or None,
        ))
        await s.commit()

    bucket = _cache.setdefault(employee_key, [])
    bucket.insert(0, content)
    if len(bucket) > _CACHE_SIZE:
        bucket.pop()


async def save_session_summary(session: "GroupSession") -> None:
    """会话结束后，将摘要写入所有员工的长期记忆。"""
    summary = getattr(session, "summary", "") or ""
    if not summary:
        return

    employees = [p for p in session.participants if not p.startswith("user")]
    if not employees:
        return

    template = getattr(session, "template", "free") or "free"
    # embedding 只生成一次，所有员工共用同一向量
    embedding = await _embed(summary)

    log.info("memory_repo: saving summary for %d employees session=%s embed=%s",
             len(employees), session.id[:8], "yes" if embedding else "no")

    async with AsyncSessionLocal() as s:
        for emp in employees:
            s.add(EmployeeMemory(
                employee_key=emp,
                content=summary,
                embedding=embedding,
                session_id=session.id,
                chat_id=getattr(session, "chat_id", None),
                template=template,
            ))
        await s.commit()

    # 刷新缓存
    for emp in employees:
        bucket = _cache.setdefault(emp, [])
        bucket.insert(0, summary)
        if len(bucket) > _CACHE_SIZE:
            bucket.pop()


# ── Semantic search ───────────────────────────────────────────────────────────

async def search_semantic(
    employee_key: str,
    query: str,
    limit: int = 5,
) -> list[str]:
    """按语义相似度检索相关记忆。无 embedding 时回退时间倒序。"""
    q_vec = await _embed(query)
    if q_vec is None:
        return get_sync(employee_key)[:limit]

    async with AsyncSessionLocal() as s:
        # 使用 pgvector <=> 余弦距离运算符
        rows = (await s.execute(
            select(EmployeeMemory.content)
            .where(
                EmployeeMemory.employee_key == employee_key,
                EmployeeMemory.embedding.isnot(None),
            )
            .order_by(EmployeeMemory.embedding.op("<=>")(q_vec))
            .limit(limit)
        )).scalars().all()

    if not rows:
        return get_sync(employee_key)[:limit]
    return list(rows)


def search_semantic_sync(
    employee_key: str,
    query: str,
    limit: int = 5,
) -> list[str]:
    """同步版语义检索，供 smart_graph 节点调用（运行在 thread executor 中）。"""
    try:
        return asyncio.run(search_semantic(employee_key, query, limit))
    except RuntimeError:
        # 已在事件循环中（不应发生于 sync LangGraph 节点）
        return get_sync(employee_key)[:limit]


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
