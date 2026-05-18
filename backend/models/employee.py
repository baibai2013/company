"""Employee + global SystemConfig models."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Employee(Base):
    __tablename__ = "employee"

    key:               Mapped[str]         = mapped_column(String(64), primary_key=True)
    name:              Mapped[str]         = mapped_column(String(64), nullable=False)
    emoji:             Mapped[str | None]  = mapped_column(String(16))
    role_desc:         Mapped[str | None]  = mapped_column(Text)
    feishu_app_id:     Mapped[str | None]  = mapped_column(Text)
    feishu_app_secret: Mapped[str | None]  = mapped_column(Text)
    agent_port:        Mapped[int | None]  = mapped_column(Integer, unique=True)
    active:            Mapped[bool]        = mapped_column(Boolean, default=True)

    system_prompt:     Mapped[str | None]  = mapped_column(Text)
    persona:           Mapped[dict | None] = mapped_column(JSONB)
    llm_calls:         Mapped[dict | None] = mapped_column(JSONB)
    behavior:          Mapped[dict | None] = mapped_column(JSONB)

    avatar_url:        Mapped[str | None]  = mapped_column(Text)   # base64 data URL or external URL
    cwd:               Mapped[str | None]  = mapped_column(Text)   # 员工工作目录绝对路径，claude code 子进程 cwd

    version:           Mapped[int]         = mapped_column(Integer, default=1)
    created_at:        Mapped[datetime]    = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at:        Mapped[datetime]    = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow,
    )


class SystemConfig(Base):
    __tablename__ = "system_config"

    id:             Mapped[int]         = mapped_column(SmallInteger, primary_key=True, default=1)
    default_models: Mapped[dict | None] = mapped_column(JSONB)
    global_prompts: Mapped[dict | None] = mapped_column(JSONB)
    system:         Mapped[dict | None] = mapped_column(JSONB)
    version:        Mapped[int]         = mapped_column(Integer, default=1)
    updated_at:     Mapped[datetime]    = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow,
    )
