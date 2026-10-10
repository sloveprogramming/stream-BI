"""monitor tasks and observations

Revision ID: f6b2c9d4e8a1
Revises: e5a1b8c3d7f2
Create Date: 2026-10-08 16:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6b2c9d4e8a1'
down_revision: Union[str, Sequence[str], None] = 'e5a1b8c3d7f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('monitor_tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('owner_user_id', sa.Integer(), nullable=False),
    sa.Column('metric_sql', sa.Text(), nullable=False),
    sa.Column('investigate_goal', sa.Text(), nullable=False),
    sa.Column('rule', sa.String(length=16), nullable=False),
    sa.Column('threshold', sa.Float(), nullable=False),
    sa.Column('direction', sa.String(length=8), nullable=False),
    sa.Column('window', sa.Integer(), nullable=False),
    sa.Column('cron', sa.String(length=64), nullable=False),
    sa.Column('notify_webhook_url', sa.String(length=512), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=16), nullable=False),
    sa.Column('last_value', sa.Float(), nullable=True),
    sa.Column('last_detail', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('monitor_observations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Integer(), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['monitor_tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_monitor_obs_task_time', 'monitor_observations', ['task_id', 'observed_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_monitor_obs_task_time', table_name='monitor_observations')
    op.drop_table('monitor_observations')
    op.drop_table('monitor_tasks')
