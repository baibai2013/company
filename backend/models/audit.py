"""Audit log model."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id:          Mapped[int]         = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp:   Mapped[datetime]    = mapped_column(DateTime(timezone=True), default=_utcnow)
    actor:       Mapped[str | None]  = mapped_column(Text)
    action:      Mapped[str | None]  = mapped_column(Text)
    target_type: Mapped[str | None]  = mapped_column(Text)
    target_key:  Mapped[str | None]  = mapped_column(Text)
    field_path:  Mapped[str | None]  = mapped_column(Text)
    old_value:   Mapped[dict | None] = mapped_column(JSONB)
    new_value:   Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("audit_log_target", "target_type", "target_key", "timestamp"),
        Index("audit_log_time", "timestamp"),
    )
