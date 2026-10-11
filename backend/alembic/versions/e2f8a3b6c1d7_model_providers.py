"""模型提供方配置表

Revision ID: e2f8a3b6c1d7
Revises: d1a7c4e9b2f5
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa

revision = "e2f8a3b6c1d7"
down_revision = "d1a7c4e9b2f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("provider_kind", sa.String(16), nullable=False, server_default="api"),
        sa.Column("base_url", sa.String(255), nullable=False, server_default=""),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("planner_model", sa.String(128), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_model_providers_kind_default", "model_providers", ["kind", "is_default"])


def downgrade() -> None:
    op.drop_index("ix_model_providers_kind_default", table_name="model_providers")
    op.drop_table("model_providers")
