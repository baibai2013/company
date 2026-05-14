"""add_employee_memory

Revision ID: 7dbdd28fcb2e
Revises: 595e12d66a37
Create Date: 2026-05-15 01:17:55.337552

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7dbdd28fcb2e'
down_revision: Union[str, Sequence[str], None] = '595e12d66a37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "employee_memory",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("employee_key", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("chat_id", sa.Text(), nullable=True),
        sa.Column("template", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_employee_memory_employee_time",
        "employee_memory",
        ["employee_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_employee_memory_employee_time", table_name="employee_memory")
    op.drop_table("employee_memory")
