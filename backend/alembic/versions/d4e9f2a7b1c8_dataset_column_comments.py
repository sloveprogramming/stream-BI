"""dataset column comments

Revision ID: d4e9f2a7b1c8
Revises: c3d8e1f5a2b4
Create Date: 2026-10-08 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e9f2a7b1c8'
down_revision: Union[str, Sequence[str], None] = 'c3d8e1f5a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('datasets', sa.Column('column_comments', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('datasets', 'column_comments')
