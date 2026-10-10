"""flow_runs.meta (运行附加信息)

Revision ID: c9e5b2d7f3a4
Revises: b8d4f1a6c9e3
Create Date: 2026-10-10 23:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9e5b2d7f3a4'
down_revision: Union[str, Sequence[str], None] = 'b8d4f1a6c9e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('flow_runs', sa.Column('meta', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('flow_runs', 'meta')
