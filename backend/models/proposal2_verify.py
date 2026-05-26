"""提案 2 · 验证与门控层 ORM 模型(verifier_runs / acceptance_checks / gate_approvals)。

字段定义与 alembic/versions/wave0_b_proposal2_verify.py 严格对齐。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VerifierRun(Base):
    """验证执行记录:每次 complete_delegation 触发都会写一条。"""

    __tablename__ = "verifier_runs"

    # 主键 / 关联
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    delegation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 闸 1 · LLM verifier
    llm_verifier_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_verifier_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_verifier_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_verifier_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 闸 2 · ground truth
    ground_truth_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    ground_truth_logs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 闸 3 · human gate
    gate_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    gate_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate_decided_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate_decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    gate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 总结
    final_verdict: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 关系
    checks: Mapped[list["AcceptanceCheck"]] = relationship(
        "AcceptanceCheck",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    gate_approvals: Mapped[list["GateApproval"]] = relationship(
        "GateApproval",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_verifier_runs_delegation_attempt", "delegation_id", "attempt"),
        Index("ix_verifier_runs_verdict_started", "final_verdict", "started_at"),
    )


class AcceptanceCheck(Base):
    """ground truth 检查项明细:每个 checker 一条,便于趋势分析。"""

    __tablename__ = "acceptance_checks"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    verifier_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("verifier_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    check_name: Mapped[str] = mapped_column(Text, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    err_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    run: Mapped["VerifierRun"] = relationship(
        "VerifierRun", back_populates="checks"
    )

    __table_args__ = (
        Index("ix_acceptance_checks_run", "verifier_run_id"),
        Index("ix_acceptance_checks_name_ok", "check_name", "ok"),
    )


class GateApproval(Base):
    """飞书人审批记录(闸 3 callback 写入)。"""

    __tablename__ = "gate_approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    verifier_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("verifier_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    feishu_chat_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    feishu_msg_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    decided_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    run: Mapped["VerifierRun"] = relationship(
        "VerifierRun", back_populates="gate_approvals"
    )

    __table_args__ = (
        Index("ix_gate_approvals_run", "verifier_run_id"),
        Index("ix_gate_approvals_status_created", "status", "created_at"),
    )


__all__ = ["VerifierRun", "AcceptanceCheck", "GateApproval"]
