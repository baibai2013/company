"""提案 2 · 验证与门控层(verifier_runs / acceptance_checks / gate_approvals)

Revision ID: wave0_b_p2_verify
Revises: 6011ff98bb2c
Create Date: 2026-05-26

说明:
- 三张表对应提案 2 §3 数据模型
- delegation_id 外键暂时指向已存在的 `task` 表(主进程 alembic merge 后,
  待 stream A 的 delegations 表落地,可在后续 migration 切换为 delegations)
- 主键统一用 PostgreSQL 原生 UUID(配合 gen_random_uuid),符合提案原始 SQL
- JSONB 字段用于结构化日志和检查器输出
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'wave0_b_p2_verify'
down_revision: Union[str, Sequence[str], None] = '6011ff98bb2c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建提案 2 的三张表:验证执行记录 / 检查项明细 / 人审批记录。"""

    # 启用 pgcrypto 扩展,提供 gen_random_uuid()(幂等)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # --------------------------------------------------------------
    # verifier_runs — 每次 complete_delegation 触发都写一条记录
    # --------------------------------------------------------------
    op.create_table(
        'verifier_runs',
        # 主键:PG 原生 UUID,默认值由 gen_random_uuid() 服务端生成
        sa.Column('id', postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text('gen_random_uuid()'),
                  comment='验证运行 ID'),
        # 关联的委派/任务 ID(暂指向 task 表,待 stream A 的 delegations 落地后切换)
        sa.Column('delegation_id', sa.String(length=36),
                  sa.ForeignKey('task.id', ondelete='CASCADE'),
                  nullable=False,
                  comment='关联的 delegation/task ID'),
        sa.Column('attempt', sa.Integer(), nullable=False,
                  comment='第几次重试(从 1 开始)'),
        sa.Column('started_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False,
                  comment='验证开始时间'),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True,
                  comment='验证完成时间'),

        # 闸 1 · LLM verifier(黑盒,只看交付物)
        sa.Column('llm_verifier_status', sa.Text(), nullable=True,
                  comment='LLM verdict: pass | fail | needs_human | error'),
        sa.Column('llm_verifier_reason', sa.Text(), nullable=True,
                  comment='LLM 给出的原因(引用 spec 哪条没满足)'),
        sa.Column('llm_verifier_model', sa.Text(), nullable=True,
                  comment='使用的模型,如 claude-haiku / claude-sonnet'),
        sa.Column('llm_verifier_tokens', sa.Integer(), nullable=True,
                  comment='本次调用消耗的 token 数(成本统计)'),

        # 闸 2 · ground truth(机器可执行检查)
        sa.Column('ground_truth_status', sa.Text(), nullable=True,
                  comment='机器检查总结:ok | err | skipped'),
        sa.Column('ground_truth_logs', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True,
                  comment='结构化检查日志 {check_name: {ok, err_msg, duration_ms}}'),

        # 闸 3 · human gate(可选,acceptance_spec.gate=true 时启用)
        sa.Column('gate_required', sa.Boolean(), nullable=False,
                  server_default=sa.text('false'),
                  comment='是否需要人类 gate 审批'),
        sa.Column('gate_status', sa.Text(), nullable=True,
                  comment='gate 状态:pending | approved | rejected | timeout'),
        sa.Column('gate_decided_by', sa.Text(), nullable=True,
                  comment='做出 gate 决策的飞书 user_id(通常 CEO)'),
        sa.Column('gate_decided_at', sa.DateTime(timezone=True), nullable=True,
                  comment='gate 决策时间'),
        sa.Column('gate_reason', sa.Text(), nullable=True,
                  comment='gate 决策原因/备注'),

        # 总结
        sa.Column('final_verdict', sa.Text(), nullable=True,
                  comment='最终结论:pass | fail'),
    )
    # 复合索引:查询某 delegation 的所有 attempt
    op.create_index(
        'ix_verifier_runs_delegation_attempt',
        'verifier_runs',
        ['delegation_id', 'attempt'],
    )
    # 复合索引:按结论 + 时间趋势分析
    op.create_index(
        'ix_verifier_runs_verdict_started',
        'verifier_runs',
        ['final_verdict', 'started_at'],
    )

    # --------------------------------------------------------------
    # acceptance_checks — 每个 ground truth check 写一条,便于趋势分析
    # --------------------------------------------------------------
    op.create_table(
        'acceptance_checks',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True,
                  comment='自增主键'),
        sa.Column('verifier_run_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('verifier_runs.id', ondelete='CASCADE'),
                  nullable=False,
                  comment='所属 verifier_run'),
        sa.Column('check_name', sa.Text(), nullable=False,
                  comment="检查器名,如 'step_loadable' / 'mount_points_present'"),
        sa.Column('ok', sa.Boolean(), nullable=False,
                  comment='检查是否通过'),
        sa.Column('duration_ms', sa.Integer(), nullable=True,
                  comment='检查耗时(毫秒)'),
        sa.Column('err_msg', sa.Text(), nullable=True,
                  comment='错误信息(失败时)'),
        sa.Column('output_log', sa.Text(), nullable=True,
                  comment='检查输出日志,stdout/stderr 截断到 8KB'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False,
                  comment='创建时间'),
    )
    op.create_index(
        'ix_acceptance_checks_run',
        'acceptance_checks',
        ['verifier_run_id'],
    )
    # 索引用于"某个 check 历史失败率"等趋势查询
    op.create_index(
        'ix_acceptance_checks_name_ok',
        'acceptance_checks',
        ['check_name', 'ok'],
    )

    # --------------------------------------------------------------
    # gate_approvals — 飞书人审批闭环记录
    # --------------------------------------------------------------
    op.create_table(
        'gate_approvals',
        sa.Column('id', postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text('gen_random_uuid()'),
                  comment='审批 ID'),
        sa.Column('verifier_run_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('verifier_runs.id', ondelete='CASCADE'),
                  nullable=False,
                  comment='所属 verifier_run'),
        sa.Column('feishu_chat_id', sa.Text(), nullable=True,
                  comment='飞书会话 ID'),
        sa.Column('feishu_msg_id', sa.Text(), nullable=True,
                  comment='飞书消息 ID,用于撤回/更新卡片'),
        sa.Column('status', sa.Text(), nullable=False,
                  server_default=sa.text("'pending'"),
                  comment='审批状态:pending | approved | rejected | timeout'),
        sa.Column('decided_by', sa.Text(), nullable=True,
                  comment='做出决策的飞书 user_id'),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True,
                  comment='决策时间'),
        sa.Column('reason', sa.Text(), nullable=True,
                  comment='决策原因/备注(从飞书卡片输入框带过来)'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False,
                  comment='创建时间'),
    )
    op.create_index(
        'ix_gate_approvals_run',
        'gate_approvals',
        ['verifier_run_id'],
    )
    # 用于 supervisor 扫超时:where status='pending' and now()>created_at+24h
    op.create_index(
        'ix_gate_approvals_status_created',
        'gate_approvals',
        ['status', 'created_at'],
    )


def downgrade() -> None:
    """逆转 upgrade,顺序与依赖相反:先删子表,再删父表。"""
    op.drop_index('ix_gate_approvals_status_created', table_name='gate_approvals')
    op.drop_index('ix_gate_approvals_run', table_name='gate_approvals')
    op.drop_table('gate_approvals')

    op.drop_index('ix_acceptance_checks_name_ok', table_name='acceptance_checks')
    op.drop_index('ix_acceptance_checks_run', table_name='acceptance_checks')
    op.drop_table('acceptance_checks')

    op.drop_index('ix_verifier_runs_verdict_started', table_name='verifier_runs')
    op.drop_index('ix_verifier_runs_delegation_attempt', table_name='verifier_runs')
    op.drop_table('verifier_runs')
    # pgcrypto 扩展不删除,可能被其他地方使用
