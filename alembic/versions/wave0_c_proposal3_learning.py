"""wave0 stream C — 提案 3 自演化学习层

Revision ID: wave0_c_p3_learn
Revises: 6011ff98bb2c
Create Date: 2026-05-26

新建提案 3 §3 数据模型:
- lessons:任务结束后 retro_agent 写入的可召回教训(带 pgvector embedding)
- pattern_extracts:周级失败模式聚合(给 PM 周报用)
- evals_fixtures:评测任务定义(手编 fixture 库)
- evals_runs:评测执行结果(单个 fixture × git_sha)
- evals_batches:评测批次元数据(一次跑全套的汇总)

注意:
- pgvector 扩展已在 6011ff98bb2c 启用,这里不重复 CREATE EXTENSION
- lessons.source_task_id 外键引用现有 task 表(String(36))
- lessons.source_run_id 暂不加 FK(verifier_runs 由 stream B 落地后再补)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'wave0_c_p3_learn'
down_revision: Union[str, Sequence[str], None] = '6011ff98bb2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """建提案 3 全部 5 张表 + 必要索引。"""

    # -------------------------------------------------------------------------
    # 1) lessons:可召回教训(retro_agent 写入)
    # -------------------------------------------------------------------------
    op.create_table(
        'lessons',
        # 主键用 PG UUID,默认 gen_random_uuid()
        sa.Column('id', postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text('gen_random_uuid()')),
        # 适用员工 key;'_global' 表跨员工
        sa.Column('employee_key', sa.Text(), nullable=False),
        # 一句话教训标题
        sa.Column('title', sa.Text(), nullable=False),
        # markdown body,200-1000 token
        sa.Column('body', sa.Text(), nullable=False),
        # 标题+body embedding,语义召回用;暂时省略 pgvector,后面 op.execute 加
        # 来源任务/verifier_run(source_run_id 等 stream B 合并后再加 FK)
        sa.Column('source_task_id', sa.String(length=36),
                  sa.ForeignKey('task.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source_run_id', postgresql.UUID(as_uuid=True), nullable=True),
        # severity 1-10:retro_agent 评分,影响召回排序
        sa.Column('severity', sa.SmallInteger(), nullable=False, server_default='5'),
        # 失败模式 tag,聚类用
        sa.Column('pattern_tag', sa.Text(), nullable=True),
        # 人工置顶,永远召回
        sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
        # 后续 lesson 作废前者(链式追溯)
        sa.Column('superseded_by', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('lessons.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        # 软过期,默认 created_at + 90d(由应用层显式写)
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    )

    # embedding 列(pgvector),走 op.execute 因为 alembic Column 不直接认识 vector 类型
    op.execute("ALTER TABLE lessons ADD COLUMN embedding vector(1536)")

    op.create_index('ix_lessons_employee_time', 'lessons',
                    ['employee_key', 'created_at'])
    op.create_index('ix_lessons_pattern_tag', 'lessons', ['pattern_tag'])
    # HNSW 索引(参考 6011ff98bb2c 的做法,适合中小规模数据集)
    op.execute(
        "CREATE INDEX ix_lessons_embedding "
        "ON lessons USING hnsw (embedding vector_cosine_ops)"
    )

    # -------------------------------------------------------------------------
    # 2) pattern_extracts:周级失败模式聚合
    # -------------------------------------------------------------------------
    op.create_table(
        'pattern_extracts',
        sa.Column('id', postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text('gen_random_uuid()')),
        # 与 lessons.pattern_tag 对齐
        sa.Column('pattern_tag', sa.Text(), nullable=False),
        # "mechanical 反复忘 mount_points"
        sa.Column('title', sa.Text(), nullable=False),
        # 详细 markdown
        sa.Column('description', sa.Text(), nullable=False),
        # 触发此模式的 lesson_ids 数组
        sa.Column('sample_lessons', postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                  nullable=True),
        # 本周内出现次数
        sa.Column('occurrence', sa.Integer(), nullable=False),
        # 涉及哪些员工
        sa.Column('employees', postgresql.ARRAY(sa.Text()), nullable=True),
        # LLM 建议的修法(改 CLAUDE.md / 加 checker)
        sa.Column('suggested_fix', sa.Text(), nullable=True),
        # open | acknowledged | fixed | wontfix
        sa.Column('status', sa.Text(), nullable=False, server_default='open'),
        # 哪一周(周一日期)
        sa.Column('week_of', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_pattern_extracts_week_occ', 'pattern_extracts',
                    ['week_of', 'occurrence'])
    op.create_index('ix_pattern_extracts_tag_status', 'pattern_extracts',
                    ['pattern_tag', 'status'])

    # -------------------------------------------------------------------------
    # 3) evals_fixtures:评测任务定义
    # -------------------------------------------------------------------------
    op.create_table(
        'evals_fixtures',
        # 手编 id,例如 'mech-leg-v1'
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('title', sa.Text(), nullable=False),
        # 模拟谁接活
        sa.Column('employee_key', sa.Text(), nullable=False),
        # 模拟派活方说什么
        sa.Column('input_prompt', sa.Text(), nullable=False),
        # 复用提案 2 的 acceptance spec schema
        sa.Column('acceptance_spec', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        # 黄金交付物路径(可选,做精确比对)
        sa.Column('golden_outputs', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
        # 1=核心 / 2=常用 / 3=长尾
        sa.Column('tier', sa.SmallInteger(), nullable=False, server_default='2'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )

    # -------------------------------------------------------------------------
    # 4) evals_runs:评测执行结果(单个 fixture × git_sha)
    # -------------------------------------------------------------------------
    op.create_table(
        'evals_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('fixture_id', sa.Text(),
                  sa.ForeignKey('evals_fixtures.id', ondelete='CASCADE'),
                  nullable=False),
        # 哪个 git 版本跑的
        sa.Column('git_sha', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        # 复用提案 2 的判定:pass | fail | needs_human
        sa.Column('verifier_verdict', sa.Text(), nullable=True),
        # 0.0 - 1.0
        sa.Column('ground_truth_pass_rate', sa.Float(), nullable=True),
        # 走了几轮 verifier 重试
        sa.Column('iterations', sa.Integer(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('token_usage', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
    )
    op.create_index('ix_evals_runs_fixture_sha', 'evals_runs',
                    ['fixture_id', 'git_sha'])
    op.create_index('ix_evals_runs_sha_verdict', 'evals_runs',
                    ['git_sha', 'verifier_verdict'])

    # -------------------------------------------------------------------------
    # 5) evals_batches:批次元数据(一次跑全套的汇总)
    # -------------------------------------------------------------------------
    op.create_table(
        'evals_batches',
        sa.Column('id', postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('git_sha', sa.Text(), nullable=False),
        # 'cron-monthly' / 'manual' / 'ci-pr-123'
        sa.Column('triggered_by', sa.Text(), nullable=False),
        sa.Column('fixture_count', sa.Integer(), nullable=False),
        sa.Column('pass_count', sa.Integer(), nullable=True),
        sa.Column('pass_rate', sa.Float(), nullable=True),
        sa.Column('avg_iterations', sa.Float(), nullable=True),
        sa.Column('avg_token_usage', sa.Float(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_evals_batches_sha', 'evals_batches', ['git_sha'])
    op.create_index('ix_evals_batches_started', 'evals_batches', ['started_at'])


def downgrade() -> None:
    """逆向删除:按依赖顺序 drop。"""
    # evals_batches 与 evals_runs 都依赖 evals_fixtures(后者只被 runs 引用)
    op.drop_index('ix_evals_batches_started', table_name='evals_batches')
    op.drop_index('ix_evals_batches_sha', table_name='evals_batches')
    op.drop_table('evals_batches')

    op.drop_index('ix_evals_runs_sha_verdict', table_name='evals_runs')
    op.drop_index('ix_evals_runs_fixture_sha', table_name='evals_runs')
    op.drop_table('evals_runs')

    op.drop_table('evals_fixtures')

    op.drop_index('ix_pattern_extracts_tag_status', table_name='pattern_extracts')
    op.drop_index('ix_pattern_extracts_week_occ', table_name='pattern_extracts')
    op.drop_table('pattern_extracts')

    # lessons 的 hnsw 索引走 raw SQL 删
    op.execute("DROP INDEX IF EXISTS ix_lessons_embedding")
    op.drop_index('ix_lessons_pattern_tag', table_name='lessons')
    op.drop_index('ix_lessons_employee_time', table_name='lessons')
    op.drop_table('lessons')
