"""血缘事件表

Revision ID: f3a9b4c2d5e8
Revises: e2f8a3b6c1d7
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a9b4c2d5e8"
down_revision = "e2f8a3b6c1d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lineage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("job_name", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("flow_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_lineage_events_job_name", "lineage_events", ["job_name"])
    op.create_index("ix_lineage_events_run_id", "lineage_events", ["run_id"])
    op.create_index("ix_lineage_events_flow_id", "lineage_events", ["flow_id"])


def downgrade() -> None:
    op.drop_index("ix_lineage_events_flow_id", table_name="lineage_events")
    op.drop_index("ix_lineage_events_run_id", table_name="lineage_events")
    op.drop_index("ix_lineage_events_job_name", table_name="lineage_events")
    op.drop_table("lineage_events")
