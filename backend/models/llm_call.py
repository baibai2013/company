"""LLM call record (used by v2 — observability/stats)."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LlmCall(Base):
    __tablename__ = "llm_call"

    id:                Mapped[int]            = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp:         Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=_utcnow)
    employee_key:      Mapped[str | None]     = mapped_column(Text)
    call_type:         Mapped[str | None]     = mapped_column(Text)
    model:             Mapped[str | None]     = mapped_column(Text)
    prompt_tokens:     Mapped[int | None]     = mapped_column(Integer)
    completion_tokens: Mapped[int | None]     = mapped_column(Integer)
    latency_ms:        Mapped[int | None]     = mapped_column(Integer)
    cost_usd:          Mapped[float | None]   = mapped_column(Numeric(10, 6))
    success:           Mapped[bool | None]    = mapped_column(Boolean)
    session_id:        Mapped[str | None]     = mapped_column(Text)
    error_msg:         Mapped[str | None]     = mapped_column(Text)

    __table_args__ = (
        Index("llm_call_emp_time", "employee_key", "timestamp"),
        Index("llm_call_time", "timestamp"),
    )
