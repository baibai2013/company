"""Wave 4 · 提案 4 §5.5 — DRBackup 元数据 CRUD 仓库。

走 SQLAlchemy 2.0 async ORM(本表无 pgvector / ARRAY,不需要绕 codec)。
仅暴露应用层会用到的 4 个方法:
  - ``create``        新建一条备份记录(status='in_progress')
  - ``mark_success``  标成功 + 写 size_bytes / s3_key / pg_db_size
  - ``mark_failed``   标失败 + 写 note(异常摘要)
  - ``latest``        取最新一条 status='success' 的记录(供 restore 用)
  - ``list_recent``   列最近 N 条(运维 / 看板)

dev pg 不可用时,调用方 swallow + log.warning,本仓库不做静默降级。
"""
from __future__ import annotations

import logging

from sqlalchemy import desc, select

from backend.core.db import AsyncSessionLocal
from backend.models.dr import DRBackup

log = logging.getLogger(__name__)


async def create(*, s3_key: str, note: str | None = None) -> DRBackup:
    """开一条 in_progress 记录,返回 refreshed ORM。"""
    async with AsyncSessionLocal() as s:
        row = DRBackup(s3_key=s3_key, status="in_progress", note=note)
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def mark_success(
    backup_id: int,
    *,
    size_bytes: int,
    pg_db_size_at_backup: int | None = None,
    s3_key: str | None = None,
) -> DRBackup | None:
    """成功收尾:status=success + size + 可选覆盖 s3_key。"""
    async with AsyncSessionLocal() as s:
        row = await s.get(DRBackup, backup_id)
        if row is None:
            return None
        row.status = "success"
        row.size_bytes = size_bytes
        if pg_db_size_at_backup is not None:
            row.pg_db_size_at_backup = pg_db_size_at_backup
        if s3_key is not None:
            row.s3_key = s3_key
        await s.commit()
        await s.refresh(row)
    return row


async def mark_failed(backup_id: int, *, note: str) -> DRBackup | None:
    """失败收尾:status=failed + note。"""
    async with AsyncSessionLocal() as s:
        row = await s.get(DRBackup, backup_id)
        if row is None:
            return None
        row.status = "failed"
        # 在已有 note 后追加,避免覆盖之前调用方写的上下文
        row.note = (f"{row.note}\n" if row.note else "") + note
        await s.commit()
        await s.refresh(row)
    return row


async def latest() -> DRBackup | None:
    """返回最新的 status='success' 备份。dr_restore 用。"""
    async with AsyncSessionLocal() as s:
        row = (await s.execute(
            select(DRBackup)
            .where(DRBackup.status == "success")
            .order_by(desc(DRBackup.created_at))
            .limit(1)
        )).scalar_one_or_none()
    return row


async def list_recent(limit: int = 30) -> list[DRBackup]:
    """列最近 N 条(任意状态),按 created_at 降序。"""
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DRBackup)
            .order_by(desc(DRBackup.created_at))
            .limit(limit)
        )).scalars().all()
    return list(rows)


__all__ = ["create", "mark_success", "mark_failed", "latest", "list_recent"]
