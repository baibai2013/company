"""wave0_a_proposal1_state — 上下文与状态层数据底座

提案 1 §3.1:
- 新建 task_context 表(任务级共享上下文 + pgvector 语义检索)
- 新建 delegations 表(派活状态机)
- 新建 delegation_events 表(派活事件流,审计 + retro 输入)
- 给 employee_memory 增补 importance / source_task_id / pinned 三字段

Revision ID: wave0_a_p1_state
Revises: 6011ff98bb2c
Create Date: 2026-05-26 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'wave0_a_p1_state'
down_revision: Union[str, Sequence[str], None] = '6011ff98bb2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. employee_memory 增补字段(L1 长期记忆评分 / 溯源 / 置顶)
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE employee_memory "
        "ADD COLUMN IF NOT EXISTS importance SMALLINT DEFAULT 5"
    )  # 1-10,summarizer 抽取时打分
    op.execute(
        "ALTER TABLE employee_memory "
        "ADD COLUMN IF NOT EXISTS source_task_id UUID"
    )  # 这条记忆来自哪个任务
    op.execute(
        "ALTER TABLE employee_memory "
        "ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT FALSE"
    )  # 人工置顶,不参与 recency 衰减

    # ------------------------------------------------------------------
    # 2. task_context — 任务级共享上下文
    # ------------------------------------------------------------------
    op.create_table(
        "task_context",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False,
                  comment="自增主键"),
        sa.Column("task_id", postgresql.UUID(as_uuid=False), nullable=False,
                  comment="根任务 id(对齐 task.id 字符串形式 UUID)"),
        sa.Column("parent_task_id", postgresql.UUID(as_uuid=False), nullable=True,
                  comment="子任务父级 id"),
        sa.Column("employee_key", sa.Text(), nullable=False,
                  comment="谁说的 / 谁做的"),
        sa.Column("role", sa.Text(), nullable=False,
                  comment="speak | act | decide | deliver"),
        sa.Column("content_chunk", sa.Text(), nullable=False,
                  comment="markdown,512-2048 token"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False,
                  comment="写入时刻"),
        sa.PrimaryKeyConstraint("id"),
    )
    # pgvector embedding(在 create_table 之外加,sqlalchemy 不便直接处理 vector 列)
    op.execute("ALTER TABLE task_context ADD COLUMN embedding vector(1536)")

    # 索引:按任务时间线浏览
    op.create_index(
        "ix_task_context_task_time",
        "task_context",
        ["task_id", "created_at"],
    )
    # 语义检索索引(项目惯例:hnsw,与 employee_memory 对齐)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_task_context_embedding "
        "ON task_context USING hnsw (embedding vector_cosine_ops)"
    )

    # ------------------------------------------------------------------
    # 3. delegations — 派活状态机
    # ------------------------------------------------------------------
    op.create_table(
        "delegations",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False,
                  server_default=sa.text("gen_random_uuid()"),
                  comment="委派 id"),
        sa.Column("from_employee", sa.Text(), nullable=False,
                  comment="派活方 employee_key"),
        sa.Column("to_employee", sa.Text(), nullable=False,
                  comment="接活方 employee_key"),
        sa.Column("parent_task_id", postgresql.UUID(as_uuid=False), nullable=False,
                  comment="关联到根任务 id"),
        sa.Column("title", sa.Text(), nullable=False,
                  comment="一句话标题"),
        sa.Column("content", sa.Text(), nullable=False,
                  comment="完整委派内容(markdown)"),
        sa.Column("acceptance_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment="验收标准,提案 2 验证器消费"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True,
                  comment="SLA 截止时间"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending",
                  comment="pending|claimed|in_progress|done|escalated|cancelled"),
        sa.Column("artifacts", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment="接活方交付的文件 / 链接 / 摘要"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True,
                  comment="认领时刻"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True,
                  comment="开始处理时刻"),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True,
                  comment="完成时刻"),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True,
                  comment="升级到人工时刻"),
        sa.Column("last_nudge_at", sa.DateTime(timezone=True), nullable=True,
                  comment="最近一次自动催办时刻"),
        sa.Column("nudge_count", sa.Integer(), nullable=False, server_default="0",
                  comment="累计催办次数"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False,
                  comment="创建时刻"),
        sa.PrimaryKeyConstraint("id"),
    )
    # 索引:派活方视角
    op.create_index(
        "ix_delegations_from_status",
        "delegations",
        ["from_employee", "status"],
    )
    # 索引:接活方视角
    op.create_index(
        "ix_delegations_to_status",
        "delegations",
        ["to_employee", "status"],
    )
    # 索引:任务视角
    op.create_index(
        "ix_delegations_parent_status",
        "delegations",
        ["parent_task_id", "status"],
    )
    # 部分索引:守护协程扫描"在飞中且要看 due_at"的记录
    op.create_index(
        "ix_delegations_inflight_due",
        "delegations",
        ["status", "due_at"],
        postgresql_where=sa.text("status IN ('pending', 'claimed', 'in_progress')"),
    )

    # ------------------------------------------------------------------
    # 4. delegation_events — 派活事件流
    # ------------------------------------------------------------------
    op.create_table(
        "delegation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False,
                  comment="自增主键"),
        sa.Column("delegation_id", postgresql.UUID(as_uuid=False), nullable=False,
                  comment="所属委派"),
        sa.Column("event_type", sa.Text(), nullable=False,
                  comment="created|claimed|progress_update|nudged|escalated|done|reopened"),
        sa.Column("actor", sa.Text(), nullable=False,
                  comment="员工 key 或 'system'"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment="事件附加数据(进度文本 / 催办内容 / 交付物等)"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False,
                  comment="事件时刻"),
        sa.ForeignKeyConstraint(
            ["delegation_id"], ["delegations.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_delegation_events_delegation_time",
        "delegation_events",
        ["delegation_id", "created_at"],
    )


def downgrade() -> None:
    # 顺序与 upgrade 相反
    op.drop_index("ix_delegation_events_delegation_time", table_name="delegation_events")
    op.drop_table("delegation_events")

    op.drop_index("ix_delegations_inflight_due", table_name="delegations")
    op.drop_index("ix_delegations_parent_status", table_name="delegations")
    op.drop_index("ix_delegations_to_status", table_name="delegations")
    op.drop_index("ix_delegations_from_status", table_name="delegations")
    op.drop_table("delegations")

    op.execute("DROP INDEX IF EXISTS ix_task_context_embedding")
    op.drop_index("ix_task_context_task_time", table_name="task_context")
    op.drop_table("task_context")

    op.execute("ALTER TABLE employee_memory DROP COLUMN IF EXISTS pinned")
    op.execute("ALTER TABLE employee_memory DROP COLUMN IF EXISTS source_task_id")
    op.execute("ALTER TABLE employee_memory DROP COLUMN IF EXISTS importance")
