"""wave4 — 灾备 DR 备份元数据表(提案 4 §5.5)

Revision ID: wave4_dr_backups
Revises: wave0_merge
Create Date: 2026-05-26

W4-C 落地 backend.models.dr.DRBackup,本 revision 把表加入 schema。
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "wave4_dr_backups"
down_revision: Union[str, Sequence[str], None] = "wave0_merge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dr_backups",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'in_progress'"),
        ),
        sa.Column("pg_db_size_at_backup", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    # status 上加索引(按状态扫 in_progress / failed 备份)
    op.create_index(
        "ix_dr_backups_status_created", "dr_backups", ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dr_backups_status_created", table_name="dr_backups")
    op.drop_table("dr_backups")
