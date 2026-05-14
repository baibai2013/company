"""add_embedding_to_employee_memory

Revision ID: 6011ff98bb2c
Revises: 7dbdd28fcb2e
Create Date: 2026-05-15 01:35:00.111002

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6011ff98bb2c'
down_revision: Union[str, Sequence[str], None] = '7dbdd28fcb2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 启用 pgvector 扩展（幂等，已存在则忽略）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 添加 embedding 列（可空，旧记录无向量时回退到时间排序）
    op.execute("ALTER TABLE employee_memory ADD COLUMN IF NOT EXISTS embedding vector(1536)")

    # HNSW 索引：增量构建，适合中小规模数据集，无需预先分桶
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_employee_memory_embedding "
        "ON employee_memory USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_employee_memory_embedding")
    op.execute("ALTER TABLE employee_memory DROP COLUMN IF EXISTS embedding")
