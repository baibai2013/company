"""提案 3 · pattern_extracts 表 CRUD 仓库。

pattern_extracts 表由 ``pattern_extractor`` 周一 cron 写入,作为 PM 周报输入。
每条 pattern 包含:
- ``pattern_tag``:模式标签(同 lessons.pattern_tag)
- ``occurrence``:本周出现次数
- ``sample_lessons``:触发它的 lesson_ids 数组(取前 5 条)
- ``employees``:覆盖到的员工 keys(去重)
- ``status``:open / acknowledged / fixed / wontfix
- ``week_of``:对齐到周一的 date

为什么用同步 psycopg + thread executor:
    与 ``backend/repos/kb_repo.py`` / ``lessons_repo.py`` 同因 — 避开 SQLAlchemy
    ORM + asyncpg + pgvector 三方耦合的脆弱面,统一短连接 + ``::`` cast 风格。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.core.config import settings

log = logging.getLogger(__name__)


# ── 常量 ──────────────────────────────────────────────────────────────────────

ALLOWED_STATUS = {"open", "acknowledged", "fixed", "wontfix"}


# ── 同步内核(在 thread executor 里跑)──────────────────────────────────────


def _conn() -> psycopg.Connection:
    url = settings.database_url_sync.replace("+psycopg", "")
    return psycopg.connect(url)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(row: dict) -> dict:
    out = dict(row)
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    if out.get("sample_lessons") is not None:
        out["sample_lessons"] = [str(x) for x in out["sample_lessons"]]
    return out


def _create_sync(
    *,
    pattern_tag: str,
    title: str,
    description: str,
    sample_lessons: list[str | uuid.UUID] | None,
    occurrence: int,
    employees: list[str] | None,
    suggested_fix: str | None,
    week_of: date,
    status: str,
) -> str:
    if status not in ALLOWED_STATUS:
        raise ValueError(f"unknown status: {status!r}")

    new_id = uuid.uuid4()
    samples = (
        [str(x) for x in sample_lessons] if sample_lessons else None
    )
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pattern_extracts
                (id, pattern_tag, title, description,
                 sample_lessons, occurrence, employees,
                 suggested_fix, status, week_of)
            VALUES (%s, %s, %s, %s,
                    %s::uuid[], %s, %s,
                    %s, %s, %s)
            """,
            (
                str(new_id),
                pattern_tag,
                title,
                description,
                samples,
                occurrence,
                employees,
                suggested_fix,
                status,
                week_of,
            ),
        )
        c.commit()
    return str(new_id)


def _list_for_week_sync(week_of: date) -> list[dict]:
    with _conn() as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, pattern_tag, title, description,
                   sample_lessons, occurrence, employees,
                   suggested_fix, status, week_of, created_at
            FROM pattern_extracts
            WHERE week_of = %s
            ORDER BY occurrence DESC, pattern_tag ASC
            """,
            (week_of,),
        )
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def _get_sync(pattern_id: uuid.UUID | str) -> dict | None:
    with _conn() as c, c.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, pattern_tag, title, description,
                   sample_lessons, occurrence, employees,
                   suggested_fix, status, week_of, created_at
            FROM pattern_extracts
            WHERE id = %s
            """,
            (str(pattern_id),),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def _update_status_sync(
    pattern_id: uuid.UUID | str,
    *,
    status: str,
) -> int:
    if status not in ALLOWED_STATUS:
        raise ValueError(f"unknown status: {status!r}")
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE pattern_extracts SET status = %s WHERE id = %s",
            (status, str(pattern_id)),
        )
        n = cur.rowcount
        c.commit()
    return n


def _delete_sync(pattern_id: uuid.UUID | str) -> int:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM pattern_extracts WHERE id = %s",
            (str(pattern_id),),
        )
        n = cur.rowcount
        c.commit()
    return n


def _delete_for_week_sync(week_of: date) -> int:
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM pattern_extracts WHERE week_of = %s",
            (week_of,),
        )
        n = cur.rowcount
        c.commit()
    return n


# ── async 包装 ────────────────────────────────────────────────────────────────


async def create(
    *,
    pattern_tag: str,
    title: str,
    description: str,
    sample_lessons: list[str | uuid.UUID] | None = None,
    occurrence: int,
    employees: list[str] | None = None,
    suggested_fix: str | None = None,
    week_of: date,
    status: str = "open",
) -> str:
    """新建一条 pattern_extract,返回新 id 字符串。"""
    return await asyncio.to_thread(
        _create_sync,
        pattern_tag=pattern_tag,
        title=title,
        description=description,
        sample_lessons=sample_lessons,
        occurrence=occurrence,
        employees=employees,
        suggested_fix=suggested_fix,
        week_of=week_of,
        status=status,
    )


async def list_for_week(week_of: date) -> list[dict]:
    """列出某周(week_of=周一)所有 pattern,occurrence 倒序。"""
    return await asyncio.to_thread(_list_for_week_sync, week_of)


async def get(pattern_id: uuid.UUID | str) -> dict | None:
    """按 id 取单条 pattern;不存在返回 None。"""
    return await asyncio.to_thread(_get_sync, pattern_id)


async def update_status(pattern_id: uuid.UUID | str, *, status: str) -> int:
    """切 status(open / acknowledged / fixed / wontfix),返回受影响行数。"""
    return await asyncio.to_thread(_update_status_sync, pattern_id, status=status)


async def delete(pattern_id: uuid.UUID | str) -> int:
    """物理删除一条 pattern(测试清理用),返回受影响行数。"""
    return await asyncio.to_thread(_delete_sync, pattern_id)


async def delete_for_week(week_of: date) -> int:
    """删某周全部 pattern(用于 cron rerun 时幂等重写),返回受影响行数。"""
    return await asyncio.to_thread(_delete_for_week_sync, week_of)


__all__ = [
    "create",
    "list_for_week",
    "get",
    "update_status",
    "delete",
    "delete_for_week",
    "ALLOWED_STATUS",
]
