"""提案 4 横切补强 — RAG / MCP trace / routing 的 SQLAlchemy 模型。

覆盖:
- §1 RAG:KbDocument(L2 公司规范 / L3 领域知识共用)、KbRetrievalLog(召回审计)
- §2 MCP 治理:ToolCallLog(全 trace)、ToolFailureQueue(失败队列)
- §3 多 agent 编排:RoutingDecision(supervisor 路由决策审计)

实现要点:
- 用 SQLAlchemy 2.0 风格 Mapped + mapped_column。
- KbDocument.embedding 复用 pgvector(参照 backend/models/memory.py)。
- ARRAY 字段使用 sqlalchemy.dialects.postgresql.ARRAY,维度信息延后给 DB 校验。
- args / meta 用 JSONB,result_summary 仍按文档约定保留为 TEXT。
- 表/索引由 alembic/versions/wave0_d_proposal4_crosscut.py 创建,本文件
  仅声明 ORM 映射,不重复声明索引。
"""
from datetime import datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# §1 RAG — kb_documents / kb_retrieval_log
# ---------------------------------------------------------------------------


class KbDocument(Base):
    """L2 公司规范 / L3 领域知识共用文档表(物理同表,domain_tag 区分)。"""

    __tablename__ = "kb_documents"

    id:           Mapped[int]               = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_path:  Mapped[str]               = mapped_column(Text, nullable=False)
    source_type:  Mapped[str]               = mapped_column(Text, nullable=False)
    domain_tag:   Mapped[str | None]        = mapped_column(Text)
    role_filter:  Mapped[list[str] | None]  = mapped_column(ARRAY(Text))
    title:        Mapped[str | None]        = mapped_column(Text)
    body:         Mapped[str]               = mapped_column(Text, nullable=False)
    embedding:    Mapped[list | None]       = mapped_column(Vector(1536), nullable=True)
    meta:         Mapped[dict | None]       = mapped_column(JSONB)
    indexed_at:   Mapped[datetime]          = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class KbRetrievalLog(Base):
    """RAG 召回 trace — 审计为什么命中/没命中,反哺 retro。"""

    __tablename__ = "kb_retrieval_log"

    id:             Mapped[int]              = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id:        Mapped[str | None]       = mapped_column(UUID(as_uuid=False))
    employee_key:   Mapped[str]              = mapped_column(Text, nullable=False)
    query:          Mapped[str]              = mapped_column(Text, nullable=False)
    layer:          Mapped[str]              = mapped_column(Text, nullable=False)  # 'L2' | 'L3'
    hit_doc_ids:    Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    hit_scores:     Mapped[list[float] | None] = mapped_column(ARRAY(Float))
    injected_chars: Mapped[int | None]       = mapped_column(Integer)
    created_at:     Mapped[datetime]         = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# §2 MCP 治理 — tool_call_log / tool_failure_queue
# ---------------------------------------------------------------------------


class ToolCallLog(Base):
    """MCP 工具调用全 trace:成功/失败/越权/超时全入此表。"""

    __tablename__ = "tool_call_log"

    id:             Mapped[int]         = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id:        Mapped[str | None]  = mapped_column(UUID(as_uuid=False))
    employee_key:   Mapped[str]         = mapped_column(Text, nullable=False)
    server_name:    Mapped[str]         = mapped_column(Text, nullable=False)
    tool_name:      Mapped[str]         = mapped_column(Text, nullable=False)
    args:           Mapped[Any | None]  = mapped_column(JSONB)
    result_summary: Mapped[str | None]  = mapped_column(Text)
    status:         Mapped[str]         = mapped_column(Text, nullable=False)  # ok/error/timeout/denied
    duration_ms:    Mapped[int | None]  = mapped_column(Integer)
    error_class:    Mapped[str | None]  = mapped_column(Text)
    created_at:     Mapped[datetime]    = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


class ToolFailureQueue(Base):
    """工具失败队列 — 供提案 3 retro_agent 消费,产出工具相关 lesson。"""

    __tablename__ = "tool_failure_queue"

    id:                 Mapped[int]              = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    log_id:             Mapped[int | None]       = mapped_column(
        BigInteger, ForeignKey("tool_call_log.id"), nullable=True
    )
    retro_consumed_at:  Mapped[datetime | None]  = mapped_column(DateTime(timezone=True))
    created_at:         Mapped[datetime]         = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# §3 多 agent 编排 — routing_decisions
# ---------------------------------------------------------------------------


class RoutingDecision(Base):
    """Supervisor 任务路由决策审计 — "为什么派给他不派给她"。"""

    __tablename__ = "routing_decisions"

    id:                  Mapped[int]               = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id:             Mapped[str]               = mapped_column(UUID(as_uuid=False), nullable=False)
    step_idx:            Mapped[int]               = mapped_column(Integer, nullable=False)
    candidate_employees: Mapped[list[str] | None]  = mapped_column(ARRAY(Text))
    chosen_employee:     Mapped[str]               = mapped_column(Text, nullable=False)
    reason:              Mapped[str | None]        = mapped_column(Text)
    routed_at:           Mapped[datetime]          = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )


__all__ = [
    "KbDocument",
    "KbRetrievalLog",
    "ToolCallLog",
    "ToolFailureQueue",
    "RoutingDecision",
]
