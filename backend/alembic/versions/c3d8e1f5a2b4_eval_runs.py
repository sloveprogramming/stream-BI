"""eval run summary

Revision ID: c3d8e1f5a2b4
Revises: b1f7a4c2d9e3
Create Date: 2026-10-08 15:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d8e1f5a2b4'
down_revision: Union[str, Sequence[str], None] = 'b1f7a4c2d9e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('eval_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('batch_id', sa.String(length=40), nullable=False),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('sql_model', sa.String(length=64), nullable=False),
    sa.Column('planner_model', sa.String(length=64), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('correct', sa.Integer(), nullable=False),
    sa.Column('accuracy', sa.Float(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('batch_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('eval_runs')
