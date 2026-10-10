"""ai_llm_cache (LLM 结果缓存)

Revision ID: d1a7c4e9b2f5
Revises: c9e5b2d7f3a4
Create Date: 2026-10-10 23:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd1a7c4e9b2f5'
down_revision: Union[str, Sequence[str], None] = 'c9e5b2d7f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ai_llm_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cache_key', sa.String(length=200), nullable=False),
        sa.Column('input_hash', sa.String(length=64), nullable=False),
        sa.Column('output', sa.JSON(), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cache_key', 'input_hash'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('ai_llm_cache')
