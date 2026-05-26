"""wave0_d 提案 4 横切补强 — RAG / MCP trace / routing schema

Revision ID: wave0_d_p4_cross
Revises: 6011ff98bb2c
Create Date: 2026-05-26

落地提案 4 横切系统补强中需要 schema 的三块:
- §1 RAG: kb_documents, kb_retrieval_log
- §2 MCP 治理: tool_call_log, tool_failure_queue
- §3 多 agent 编排: routing_decisions

注意:
- pgvector 扩展已在 6011ff98bb2c 启用,本迁移不再 CREATE EXTENSION。
- ivfflat 索引使用原生 SQL 创建(alembic 不直接支持 vector ops 类)。
- 所有 down 操作可逆。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'wave0_d_p4_cross'
down_revision: Union[str, Sequence[str], None] = '6011ff98bb2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------
    # §1 RAG — kb_documents (L2 公司规范 / L3 领域知识共用)
    # ---------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS kb_documents (
          id BIGSERIAL PRIMARY KEY,
          source_path TEXT NOT NULL,
          source_type TEXT NOT NULL,
          domain_tag TEXT,
          role_filter TEXT[],
          title TEXT,
          body TEXT NOT NULL,
          embedding vector(1536),
          meta JSONB,
          indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ivfflat 向量索引 — pgvector 提供的近似最近邻索引,适合 < 10万级文档量
    op.execute("""
        CREATE INDEX IF NOT EXISTS kb_docs_embedding_idx
          ON kb_documents
          USING ivfflat (embedding vector_cosine_ops)
          WITH (lists = 100)
    """)

    # role_filter 是 TEXT[],GIN 索引支持 ANY/&& 高效过滤
    op.execute("""
        CREATE INDEX IF NOT EXISTS kb_docs_role_idx
          ON kb_documents
          USING GIN (role_filter)
    """)

    # domain_tag 普通 B-tree(L2/L3 区分常用)
    op.execute("""
        CREATE INDEX IF NOT EXISTS kb_docs_domain_idx
          ON kb_documents (domain_tag)
    """)

    # ---------------------------------------------------------------
    # §1 RAG — kb_retrieval_log (召回 trace,审计/反向更新 KB)
    # ---------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS kb_retrieval_log (
          id BIGSERIAL PRIMARY KEY,
          task_id UUID,
          employee_key TEXT NOT NULL,
          query TEXT NOT NULL,
          layer TEXT NOT NULL,
          hit_doc_ids BIGINT[],
          hit_scores FLOAT[],
          injected_chars INT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ---------------------------------------------------------------
    # §2 MCP 治理 — tool_call_log (工具调用全 trace)
    # ---------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_call_log (
          id BIGSERIAL PRIMARY KEY,
          task_id UUID,
          employee_key TEXT NOT NULL,
          server_name TEXT NOT NULL,
          tool_name TEXT NOT NULL,
          args JSONB,
          result_summary TEXT,
          status TEXT NOT NULL,
          duration_ms INT,
          error_class TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS tool_log_task_idx
          ON tool_call_log (task_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS tool_log_employee_idx
          ON tool_call_log (employee_key, created_at DESC)
    """)

    # 失败专用部分索引,加速 retro 拉取失败明细
    op.execute("""
        CREATE INDEX IF NOT EXISTS tool_log_failures_idx
          ON tool_call_log (status, created_at DESC)
          WHERE status != 'ok'
    """)

    # ---------------------------------------------------------------
    # §2 MCP 治理 — tool_failure_queue (失败队列,供提案 3 retro 消费)
    # ---------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_failure_queue (
          id BIGSERIAL PRIMARY KEY,
          log_id BIGINT REFERENCES tool_call_log(id),
          retro_consumed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ---------------------------------------------------------------
    # §3 多 agent 编排 — routing_decisions (supervisor 决策审计)
    # ---------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS routing_decisions (
          id BIGSERIAL PRIMARY KEY,
          task_id UUID NOT NULL,
          step_idx INT NOT NULL,
          candidate_employees TEXT[],
          chosen_employee TEXT NOT NULL,
          reason TEXT,
          routed_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    # 反向顺序:先删依赖表(tool_failure_queue 依赖 tool_call_log),再删主表与索引
    op.execute("DROP TABLE IF EXISTS routing_decisions")

    op.execute("DROP TABLE IF EXISTS tool_failure_queue")

    op.execute("DROP INDEX IF EXISTS tool_log_failures_idx")
    op.execute("DROP INDEX IF EXISTS tool_log_employee_idx")
    op.execute("DROP INDEX IF EXISTS tool_log_task_idx")
    op.execute("DROP TABLE IF EXISTS tool_call_log")

    op.execute("DROP TABLE IF EXISTS kb_retrieval_log")

    op.execute("DROP INDEX IF EXISTS kb_docs_domain_idx")
    op.execute("DROP INDEX IF EXISTS kb_docs_role_idx")
    op.execute("DROP INDEX IF EXISTS kb_docs_embedding_idx")
    op.execute("DROP TABLE IF EXISTS kb_documents")
