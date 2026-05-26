"""wave0 merge — 提案 1/2/3/4 schema 合流

Revision ID: wave0_merge
Revises: wave0_a_p1_state, wave0_b_p2_verify, wave0_c_p3_learn, wave0_d_p4_cross
Create Date: 2026-05-26 11:57:13.290256

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'wave0_merge'
down_revision: Union[str, Sequence[str], None] = ('wave0_a_p1_state', 'wave0_b_p2_verify', 'wave0_c_p3_learn', 'wave0_d_p4_cross')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
