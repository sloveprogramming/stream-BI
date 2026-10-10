"""data flow tables (编排层)

Revision ID: b8d4f1a6c9e3
Revises: f6b2c9d4e8a1
Create Date: 2026-10-10 22:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d4f1a6c9e3'
down_revision: Union[str, Sequence[str], None] = 'f6b2c9d4e8a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'data_flows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=128), nullable=True),
        sa.Column('description', sa.String(length=512), nullable=True),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('current_version', sa.Integer(), nullable=False),
        sa.Column('target_schema', sa.String(length=16), nullable=False),
        sa.Column('target_table', sa.String(length=128), nullable=False),
        sa.Column('output_dataset_id', sa.Integer(), nullable=True),
        sa.Column('cron', sa.String(length=64), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['output_dataset_id'], ['datasets.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'data_flow_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('flow_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('version_id', sa.String(length=40), nullable=False),
        sa.Column('dsl', sa.JSON(), nullable=False),
        sa.Column('compiled_sql', sa.Text(), nullable=True),
        sa.Column('compiled_yaml', sa.Text(), nullable=True),
        sa.Column('dbt_selector', sa.String(length=160), nullable=True),
        sa.Column('input_datasets', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['flow_id'], ['data_flows.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('flow_id', 'version'),
        sa.UniqueConstraint('version_id'),
    )
    op.create_table(
        'flow_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('flow_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.String(length=40), nullable=False),
        sa.Column('triggered_by', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('rows_out', sa.Integer(), nullable=False),
        sa.Column('elapsed_ms', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('grant_snapshot', sa.JSON(), nullable=True),
        sa.Column('trigger_user_id', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['flow_id'], ['data_flows.id'], ),
        sa.ForeignKeyConstraint(['trigger_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id'),
    )
    op.create_index('ix_flow_runs_flow_started', 'flow_runs', ['flow_id', 'started_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_flow_runs_flow_started', table_name='flow_runs')
    op.drop_table('flow_runs')
    op.drop_table('data_flow_versions')
    op.drop_table('data_flows')
