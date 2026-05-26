"""员工长期记忆模型 — 跨会话保存参与过的讨论/游戏摘要，支持 pgvector 语义检索。

Wave 0 stream A 在 alembic 层新增了 importance / source_task_id / pinned 三列以支撑提案 1
的"L1 长期记忆"打分与置顶语义,这里同步 ORM 定义,避免 DB/ORM 漂移。
"""
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, DateTime, Index, SmallInteger, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmployeeMemory(Base):
    __tablename__ = "employee_memory"

    id:             Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_key:   Mapped[str]      = mapped_column(Text, nullable=False)
    session_id:     Mapped[str | None] = mapped_column(Text)
    chat_id:        Mapped[str | None] = mapped_column(Text)
    template:       Mapped[str | None] = mapped_column(Text)   # "werewolf" / "brainstorm" / "free"
    content:        Mapped[str]      = mapped_column(Text, nullable=False)
    embedding:      Mapped[list | None] = mapped_column(Vector(1536), nullable=True)
    # ── 提案 1 wave 0 新增 ──────────────────────────────────────────────
    importance:     Mapped[int]      = mapped_column(SmallInteger, default=5, server_default="5")  # 1-10 重要度,summarizer 打分
    source_task_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)   # 这条记忆来自哪个任务
    pinned:         Mapped[bool]     = mapped_column(Boolean, default=False, server_default="false")  # 人工置顶,不参与衰减
    created_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_employee_memory_employee_time", "employee_key", "created_at"),
    )
