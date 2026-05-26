"""提案 3 自演化学习层 ORM 模型。

包含 5 个类,对应提案 3 §3 的 5 张表:
- Lesson:任务结束 retro_agent 写入的可召回教训(带 pgvector embedding)
- PatternExtract:周级失败模式聚合
- EvalsFixture:评测任务定义(手编 fixture 库)
- EvalsRun:评测执行结果(单个 fixture × git_sha)
- EvalsBatch:评测批次元数据

依赖:
- backend.core.db.Base(共享声明基类)
- pgvector.sqlalchemy.Vector(语义召回 1536 维)
- 现有 task 表(lessons.source_task_id 外键)
- verifier_runs 表暂未建,所以 source_run_id 字段保留 UUID 但不外键
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> datetime:
    """返回带时区的当前 UTC 时间。"""
    return datetime.now(timezone.utc)


# =============================================================================
# 1) Lesson:任务结束后 retro_agent 写入的教训
# =============================================================================
class Lesson(Base):
    """可召回的任务级教训(actionable lesson)。

    - employee_key:适用员工角色;'_global' 表跨员工
    - embedding:title + body 的 1536 维向量,语义召回用
    - severity:1-10,影响召回排序;同 pattern 多次发生时 bump
    - pinned:人工置顶,永远召回
    - superseded_by:链式作废(场景:发现旧 lesson 过时或错了)
    - expires_at:软过期,默认 created_at + 90d(由应用层显式写)
    """

    __tablename__ = "lessons"

    id:             Mapped[uuid.UUID]      = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    employee_key:   Mapped[str]            = mapped_column(Text, nullable=False)
    title:          Mapped[str]            = mapped_column(Text, nullable=False)
    body:           Mapped[str]            = mapped_column(Text, nullable=False)
    embedding:      Mapped[list | None]    = mapped_column(Vector(1536), nullable=True)

    # 来源任务(现有 task.id 是 String(36))
    source_task_id: Mapped[str | None]     = mapped_column(
        String(36), ForeignKey("task.id", ondelete="SET NULL"), nullable=True
    )
    # 来源 verifier_run(stream B 建表后再加 FK,这里先保留 UUID 字段)
    source_run_id:  Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    severity:       Mapped[int]            = mapped_column(SmallInteger, nullable=False, default=5)
    pattern_tag:    Mapped[str | None]     = mapped_column(Text, nullable=True)
    pinned:         Mapped[bool]           = mapped_column(Boolean, nullable=False, default=False)

    # 自引用:链式作废
    superseded_by:  Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lessons.id", ondelete="SET NULL"), nullable=True
    )

    created_at:     Mapped[datetime]       = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    expires_at:     Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_lessons_employee_time", "employee_key", "created_at"),
        Index("ix_lessons_pattern_tag", "pattern_tag"),
        # 注意:HNSW 索引由 alembic raw SQL 创建,此处不声明
    )


# =============================================================================
# 2) PatternExtract:周级失败模式聚合
# =============================================================================
class PatternExtract(Base):
    """pattern_extractor 周一 cron 写入,给 PM 周报用。

    - status 流转:open → acknowledged / fixed / wontfix
    - sample_lessons:触发此模式的 lesson_ids 数组
    """

    __tablename__ = "pattern_extracts"

    id:             Mapped[uuid.UUID]      = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pattern_tag:    Mapped[str]            = mapped_column(Text, nullable=False)
    title:          Mapped[str]            = mapped_column(Text, nullable=False)
    description:    Mapped[str]            = mapped_column(Text, nullable=False)

    sample_lessons: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    occurrence:     Mapped[int]            = mapped_column(Integer, nullable=False)
    employees:      Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    suggested_fix:  Mapped[str | None]     = mapped_column(Text, nullable=True)

    # open | acknowledged | fixed | wontfix
    status:         Mapped[str]            = mapped_column(Text, nullable=False, default="open")
    week_of:        Mapped[date]           = mapped_column(Date, nullable=False)

    created_at:     Mapped[datetime]       = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (
        Index("ix_pattern_extracts_week_occ", "week_of", "occurrence"),
        Index("ix_pattern_extracts_tag_status", "pattern_tag", "status"),
    )


# =============================================================================
# 3) EvalsFixture:评测任务定义
# =============================================================================
class EvalsFixture(Base):
    """评测 fixture(手编 id,如 'mech-leg-v1')。

    - acceptance_spec:复用提案 2 的 spec schema
    - tier:1=核心 / 2=常用 / 3=长尾
    """

    __tablename__ = "evals_fixtures"

    id:              Mapped[str]           = mapped_column(Text, primary_key=True)
    title:           Mapped[str]           = mapped_column(Text, nullable=False)
    employee_key:    Mapped[str]           = mapped_column(Text, nullable=False)
    input_prompt:    Mapped[str]           = mapped_column(Text, nullable=False)
    acceptance_spec: Mapped[dict]          = mapped_column(JSONB, nullable=False)
    golden_outputs:  Mapped[dict | None]   = mapped_column(JSONB, nullable=True)
    tier:            Mapped[int]           = mapped_column(SmallInteger, nullable=False, default=2)
    created_at:      Mapped[datetime]      = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


# =============================================================================
# 4) EvalsRun:评测执行结果(fixture × git_sha)
# =============================================================================
class EvalsRun(Base):
    """单个 fixture 在某个 git_sha 下的执行结果。

    复用提案 2 的 verifier_orchestrator,verdict 与 verifier_runs 同义。
    """

    __tablename__ = "evals_runs"

    id:                     Mapped[uuid.UUID]   = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fixture_id:             Mapped[str]         = mapped_column(
        Text, ForeignKey("evals_fixtures.id", ondelete="CASCADE"), nullable=False
    )
    git_sha:                Mapped[str]         = mapped_column(Text, nullable=False)

    started_at:             Mapped[datetime]    = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at:           Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # pass | fail | needs_human
    verifier_verdict:       Mapped[str | None]  = mapped_column(Text, nullable=True)
    ground_truth_pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    iterations:             Mapped[int | None]  = mapped_column(Integer, nullable=True)
    duration_seconds:       Mapped[int | None]  = mapped_column(Integer, nullable=True)
    token_usage:            Mapped[int | None]  = mapped_column(Integer, nullable=True)
    cost_usd:               Mapped[float | None] = mapped_column(Float, nullable=True)
    notes:                  Mapped[str | None]  = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_evals_runs_fixture_sha", "fixture_id", "git_sha"),
        Index("ix_evals_runs_sha_verdict", "git_sha", "verifier_verdict"),
    )


# =============================================================================
# 5) EvalsBatch:批次元数据(一次跑全套的汇总)
# =============================================================================
class EvalsBatch(Base):
    """一次评测批次的汇总(triggered_by 标识来源:cron / manual / ci-pr-N)。"""

    __tablename__ = "evals_batches"

    id:               Mapped[uuid.UUID]   = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    git_sha:          Mapped[str]         = mapped_column(Text, nullable=False)
    triggered_by:     Mapped[str]         = mapped_column(Text, nullable=False)
    fixture_count:    Mapped[int]         = mapped_column(Integer, nullable=False)
    pass_count:       Mapped[int | None]  = mapped_column(Integer, nullable=True)
    pass_rate:        Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_iterations:   Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_token_usage:  Mapped[float | None] = mapped_column(Float, nullable=True)

    started_at:       Mapped[datetime]    = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at:     Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_evals_batches_sha", "git_sha"),
        Index("ix_evals_batches_started", "started_at"),
    )


__all__ = [
    "Lesson",
    "PatternExtract",
    "EvalsFixture",
    "EvalsRun",
    "EvalsBatch",
]
