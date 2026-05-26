"""Wave 4 · 提案 4 §5.5 — 灾备(DR)备份元数据表。

只跟踪 postgres 备份产物的元数据(s3 对象 key / 大小 / 状态),不存
真备份内容(那在 S3 / MinIO)。配合:
  - ``infra/dr/pg_backup.sh`` 出 dump
  - ``scripts/dr_backup.py`` 上传 + 写元数据(本表)
  - ``scripts/dr_restore.py`` 从最新一条恢复

字段语义:
  - status: 'in_progress' | 'success' | 'failed'
  - s3_key: MinIO/S3 对象 key(如 ``dr/2026-05-26T02-00-00.dump``);
            没有 boto3 / s3 时仍写本地路径降级
  - size_bytes: dump 文件字节数(成功才有)
  - pg_db_size_at_backup: 备份时 dev pg 整库大小(human 字节,可空)

本模型不强制挂在 alembic 迁移里(W4-C 严格不动 alembic env.py)。
真投产时由主进程在 alembic 加 import 后产出 revision。当前测试用
Base.metadata.create_all + 显式 import 该模块即可建表。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# pg 走 BigInteger,SQLite 单测降级 Integer(SQLite 的 rowid 自增只对
# INTEGER PK 生效,BIGINT PK 不会自动 rowid)。
_AutoBigInt = BigInteger().with_variant(Integer(), "sqlite")


class DRBackup(Base):
    """postgres 备份产物元数据。"""

    __tablename__ = "dr_backups"

    id:                    Mapped[int]              = mapped_column(_AutoBigInt, primary_key=True, autoincrement=True)
    created_at:            Mapped[datetime]         = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    s3_key:                Mapped[str]              = mapped_column(Text, nullable=False)
    size_bytes:            Mapped[int | None]       = mapped_column(BigInteger)
    status:                Mapped[str]              = mapped_column(Text, nullable=False, default="in_progress")
    pg_db_size_at_backup:  Mapped[int | None]       = mapped_column(BigInteger)
    note:                  Mapped[str | None]       = mapped_column(Text)


__all__ = ["DRBackup"]
