"""提案 1 — 上下文与状态层 ORM 模型。

包含三个类:
- TaskContext     : 任务级共享上下文,跨员工可读,带 pgvector embedding
- Delegation      : 派活状态机,贯穿派活方/接活方/超时升级
- DelegationEvent : 派活事件流,审计与 retro 输入
"""
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# TaskContext — 任务级共享上下文(L2)
# --------------------------------------------------------------------------- #
class TaskContext(Base):
    """任务级共享上下文 chunk,跨员工可读;由 LLM 完成回合或工具调用关键节点写入。"""

    __tablename__ = "task_context"

    id:             Mapped[int]                = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id:        Mapped[str]                = mapped_column(UUID(as_uuid=False), nullable=False)
    parent_task_id: Mapped[str | None]         = mapped_column(UUID(as_uuid=False), nullable=True)
    employee_key:   Mapped[str]                = mapped_column(Text, nullable=False)
    role:           Mapped[str]                = mapped_column(Text, nullable=False)   # speak | act | decide | deliver
    content_chunk: Mapped[str]                 = mapped_column(Text, nullable=False)
    embedding:      Mapped[list | None]        = mapped_column(Vector(1536), nullable=True)
    created_at:     Mapped[datetime]           = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), default=_utcnow, nullable=False,
    )

    __table_args__ = (
        Index("ix_task_context_task_time", "task_id", "created_at"),
    )


# --------------------------------------------------------------------------- #
# Delegation — 派活状态机
# --------------------------------------------------------------------------- #
class Delegation(Base):
    """派活记录,完整状态机。状态机:
    pending -> claimed -> in_progress -> done
                                   \\-> escalated
                              \\-> cancelled
    """

    __tablename__ = "delegations"

    id:              Mapped[str]               = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()"),
    )
    from_employee:   Mapped[str]               = mapped_column(Text, nullable=False)
    to_employee:     Mapped[str]               = mapped_column(Text, nullable=False)
    parent_task_id:  Mapped[str]               = mapped_column(UUID(as_uuid=False), nullable=False)
    title:           Mapped[str]               = mapped_column(Text, nullable=False)
    content:         Mapped[str]               = mapped_column(Text, nullable=False)
    acceptance_spec: Mapped[dict | None]       = mapped_column(JSONB, nullable=True)
    due_at:          Mapped[datetime | None]   = mapped_column(DateTime(timezone=True), nullable=True)
    status:          Mapped[str]               = mapped_column(
        Text, nullable=False, server_default="pending", default="pending",
    )   # pending | claimed | in_progress | done | escalated | cancelled
    artifacts:       Mapped[dict | None]       = mapped_column(JSONB, nullable=True)
    claimed_at:      Mapped[datetime | None]   = mapped_column(DateTime(timezone=True), nullable=True)
    started_at:      Mapped[datetime | None]   = mapped_column(DateTime(timezone=True), nullable=True)
    done_at:         Mapped[datetime | None]   = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at:    Mapped[datetime | None]   = mapped_column(DateTime(timezone=True), nullable=True)
    last_nudge_at:   Mapped[datetime | None]   = mapped_column(DateTime(timezone=True), nullable=True)
    nudge_count:     Mapped[int]               = mapped_column(
        Integer, nullable=False, server_default="0", default=0,
    )
    created_at:      Mapped[datetime]          = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), default=_utcnow, nullable=False,
    )

    events: Mapped[list["DelegationEvent"]] = relationship(
        "DelegationEvent",
        back_populates="delegation",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DelegationEvent.created_at",
    )

    __table_args__ = (
        Index("ix_delegations_from_status", "from_employee", "status"),
        Index("ix_delegations_to_status",   "to_employee",   "status"),
        Index("ix_delegations_parent_status", "parent_task_id", "status"),
        Index(
            "ix_delegations_inflight_due",
            "status", "due_at",
            postgresql_where=text("status IN ('pending', 'claimed', 'in_progress')"),
        ),
    )


# --------------------------------------------------------------------------- #
# DelegationEvent — 事件流
# --------------------------------------------------------------------------- #
class DelegationEvent(Base):
    """派活事件流。每次状态转移、催办、进度更新都写一条,供审计与 retro。"""

    __tablename__ = "delegation_events"

    id:            Mapped[int]      = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delegation_id: Mapped[str]      = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("delegations.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type:    Mapped[str]      = mapped_column(Text, nullable=False)
    # created | claimed | progress_update | nudged | escalated | done | reopened
    actor:         Mapped[str]      = mapped_column(Text, nullable=False)
    payload:       Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), default=_utcnow, nullable=False,
    )

    delegation: Mapped["Delegation"] = relationship("Delegation", back_populates="events")

    __table_args__ = (
        Index("ix_delegation_events_delegation_time", "delegation_id", "created_at"),
    )


__all__ = ["TaskContext", "Delegation", "DelegationEvent"]
