"""员工长期记忆模型 — 跨会话保存参与过的讨论/游戏摘要。"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmployeeMemory(Base):
    __tablename__ = "employee_memory"

    id:           Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_key: Mapped[str]      = mapped_column(Text, nullable=False)
    session_id:   Mapped[str | None] = mapped_column(Text)
    chat_id:      Mapped[str | None] = mapped_column(Text)
    template:     Mapped[str | None] = mapped_column(Text)   # "werewolf" / "brainstorm" / "free"
    content:      Mapped[str]      = mapped_column(Text, nullable=False)
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_employee_memory_employee_time", "employee_key", "created_at"),
    )
