"""verified qa few-shot examples

Revision ID: e5a1b8c3d7f2
Revises: d4e9f2a7b1c8
Create Date: 2026-10-08 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a1b8c3d7f2'
down_revision: Union[str, Sequence[str], None] = 'd4e9f2a7b1c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('verified_qa',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('sql', sa.Text(), nullable=False),
    sa.Column('dataset_keys', sa.JSON(), nullable=False),
    sa.Column('tags', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('hits', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('question')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('verified_qa')
