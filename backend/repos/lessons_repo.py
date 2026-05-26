"""提案 3 · lessons 表 CRUD 仓库。

为什么用同步 psycopg + thread executor 而不是 SQLAlchemy async ORM:
    与 ``backend/repos/kb_repo.py`` 同因 — pgvector 的 asyncpg binary codec 已经
    在 ``backend/core/db.py`` 全局注册,与 SQLAlchemy ``Vector`` 列的 bind_processor
    路径冲突。短期最稳的做法是绕开 SQLAlchemy 这层,直接用 psycopg + ``::vector`` cast。

读写约定:
- ``employee_key='_global'`` 表跨员工教训
- ``embedding`` 1536 维(text-embedding-3-small),允许 NULL(写入时未生成)
- ``pinned=True`` 永远召回(即使过期)
- ``superseded_by`` 链式作废:旧 lesson 被新 lesson 顶替
- ``expires_at`` 软过期 — 默认 created_at + 90d(由应用层显式写)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.core.config import settings

log = logging.getLogger(__name__)


# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_TTL_DAYS = 90
"""lesson 默认软过期天数 — 提案 3 §3 要求 90d。"""


# ── 同步内核(在 thread executor 里跑)──────────────────────────────────────


def _conn() -> psycopg.Connection:
    """打开一个同步 psycopg 连接(短连接 call/close 风格)。"""
    url = settings.database_url_sync.replace("+psycopg", "")
    return psycopg.connect(url)


def _vec_literal(vec: list[float]) -> str:
    """``list[float]`` → ``"[0.1,0.2,...]"`` 文本字面量,供 ``::vector`` cast。"""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: dict) -> dict:
    """psycopg dict_row 输出标准化:UUID → str、score 转 float。"""
    out = dict(row)
    if "id" in out and out["id"] is not None:
        out["id"] = str(out["id"])
    if "superseded_by" in out and out["superseded_by"] is not None:
        out["superseded_by"] = str(out["superseded_by"])
    if "score" in out and out["score"] is not None:
        out["score"] = float(out["score"])
    return out


def _create_sync(
    *,
    employee_key: str,
    title: str,
    body: str,
    embedding: list[float] | None,
    source_task_id: str | None,
    source_run_id: uuid.UUID | str | None,
    severity: int,
    pattern_tag: str | None,
    pinned: bool,
    expires_at: datetime | None,
) -> str:
    new_id = uuid.uuid4()
    if expires_at is None:
        expires_at = _utcnow() + timedelta(days=DEFAULT_TTL_DAYS)

    src_run = (
        str(source_run_id) if source_run_id is not None else None
    )
    emb_param = _vec_literal(embedding) if embedding else None

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO lessons
                (id, employee_key, title, body, embedding,
                 source_task_id, source_run_id, severity, pattern_tag,
                 pinned, expires_at)
            VALUES (%s, %s, %s, %s, %s::vector,
                    %s, %s, %s, %s,
                    %s, %s)
            """,
            (
                str(new_id),
                employee_key,
                title,
                body,
                emb_param,
                source_task_id,
                src_run,
                severity,
                pattern_tag,
                pinned,
                expires_at,
            ),
        )
        c.commit()
    return str(new_id)


def _search_sync(
    *,
    query_embedding: list[float],
    employee_key: str,
    top_k: int,
    include_expired: bool,
) -> list[dict]:
    """按 cosine 距离查 top_k(含 pinned 优先 + 过期过滤)。

    SQL 思路:
        - 命中范围 = '_global' 或 当前员工
        - 默认过滤过期(``expires_at IS NULL OR expires_at > now()``)
        - pinned=true 直接放最前(severity 兜底)
    """
    where_clauses = ["employee_key IN ('_global', %(emp)s)"]
    if not include_expired:
        where_clauses.append("(expires_at IS NULL OR expires_at > now())")
    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT id, employee_key, title, body, severity, pattern_tag,
               pinned, source_task_id, source_run_id,
               created_at, expires_at,
               1 - (embedding <=> %(q)s::vector) AS score
        FROM lessons
        WHERE embedding IS NOT NULL
          AND {where_sql}
          AND superseded_by IS NULL
        ORDER BY pinned DESC,
                 (embedding <=> %(q)s::vector) ASC
        LIMIT %(top_k)s
    """
    params = {
        "q": _vec_literal(query_embedding),
        "emp": employee_key,
        "top_k": top_k,
    }
    with _conn() as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def _get_sync(lesson_id: uuid.UUID | str) -> dict | None:
    with _conn() as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, employee_key, title, body, severity, pattern_tag,
                   pinned, source_task_id, source_run_id,
                   created_at, expires_at, superseded_by
            FROM lessons
            WHERE id = %s
            """,
            (str(lesson_id),),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _list_by_pattern_sync(
    *,
    pattern_tag: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list[dict]:
    """聚合用:按 pattern_tag + 时间窗列出 lesson(给 pattern_extractor 用)。"""
    clauses: list[str] = ["superseded_by IS NULL"]
    params: dict[str, Any] = {}
    if pattern_tag is not None:
        clauses.append("pattern_tag = %(tag)s")
        params["tag"] = pattern_tag
    if since is not None:
        clauses.append("created_at >= %(since)s")
        params["since"] = since
    if until is not None:
        clauses.append("created_at < %(until)s")
        params["until"] = until
    where_sql = " AND ".join(clauses)
    sql = f"""
        SELECT id, employee_key, title, body, severity, pattern_tag,
               pinned, created_at
        FROM lessons
        WHERE {where_sql}
        ORDER BY created_at ASC
    """
    with _conn() as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def _delete_sync(lesson_id: uuid.UUID | str) -> int:
    """物理删除(测试用);生产路径请用 supersede。"""
    with _conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM lessons WHERE id = %s", (str(lesson_id),))
        n = cur.rowcount
        c.commit()
    return n


def _supersede_sync(old_id: uuid.UUID | str, new_id: uuid.UUID | str) -> None:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE lessons SET superseded_by = %s WHERE id = %s",
            (str(new_id), str(old_id)),
        )
        c.commit()


# ── async 包装 ────────────────────────────────────────────────────────────────


async def create(
    *,
    employee_key: str,
    title: str,
    body: str,
    embedding: list[float] | None = None,
    source_task_id: str | None = None,
    source_run_id: uuid.UUID | str | None = None,
    severity: int = 5,
    pattern_tag: str | None = None,
    pinned: bool = False,
    expires_at: datetime | None = None,
) -> str:
    """新建一条 lesson,返回 ``str(uuid)``。

    Args:
        employee_key: 适用员工 key;``'_global'`` 表跨员工。
        title: 短标题(召回时显示)。
        body: 详细描述,可含 markdown。
        embedding: 1536 维向量;为 None 表示尚未生成(将进入"无向量召回"分支不可达)。
        source_task_id: 可空,关联 ``task.id``。
        source_run_id: 可空,关联 ``verifier_runs.id``。
        severity: 1-10,影响排序;同 pattern 重复发生应 bump。
        pattern_tag: 模式标签,聚类用(如 ``'missing_required_files'``)。
        pinned: True 表示强制召回。
        expires_at: 默认 created_at + 90d。

    Returns:
        新 lesson 的 id 字符串。
    """
    return await asyncio.to_thread(
        _create_sync,
        employee_key=employee_key,
        title=title,
        body=body,
        embedding=embedding,
        source_task_id=source_task_id,
        source_run_id=source_run_id,
        severity=severity,
        pattern_tag=pattern_tag,
        pinned=pinned,
        expires_at=expires_at,
    )


async def search_by_embedding(
    *,
    query_embedding: list[float],
    employee_key: str,
    top_k: int = 5,
    include_expired: bool = False,
) -> list[dict]:
    """按 cosine 相似度 + employee_key 召回 top_k。

    Returns:
        ``{id, employee_key, title, body, severity, pattern_tag, pinned,
           source_task_id, source_run_id, created_at, expires_at, score}`` 列表;
        ``score`` 越大越相关(已转为 ``1 - distance``);pinned 行排在最前。
    """
    return await asyncio.to_thread(
        _search_sync,
        query_embedding=query_embedding,
        employee_key=employee_key,
        top_k=top_k,
        include_expired=include_expired,
    )


async def get(lesson_id: uuid.UUID | str) -> dict | None:
    """按 id 取单条 lesson;不存在返回 None。"""
    return await asyncio.to_thread(_get_sync, lesson_id)


async def list_by_pattern(
    *,
    pattern_tag: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """按 pattern_tag + 时间窗列出 lesson(供 Loop 3 聚类骨架用)。

    pattern_tag=None 表示不限标签(与 since/until 配合做"全表周聚合")。
    """
    return await asyncio.to_thread(
        _list_by_pattern_sync,
        pattern_tag=pattern_tag,
        since=since,
        until=until,
    )


async def delete(lesson_id: uuid.UUID | str) -> int:
    """物理删除一条 lesson(主要测试清理用),返回受影响行数。"""
    return await asyncio.to_thread(_delete_sync, lesson_id)


async def supersede(old_id: uuid.UUID | str, new_id: uuid.UUID | str) -> None:
    """把旧 lesson 标为被 new_id 顶替(链式作废,召回时排除)。"""
    await asyncio.to_thread(_supersede_sync, old_id, new_id)


__all__ = [
    "create",
    "search_by_embedding",
    "get",
    "list_by_pattern",
    "delete",
    "supersede",
    "DEFAULT_TTL_DAYS",
]
