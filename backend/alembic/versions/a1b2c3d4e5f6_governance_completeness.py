"""治理补全：租户隔离 / 权限申请与有效期 / 数据质量巡检 / 回答置信度

Revision ID: a1b2c3d4e5f6
Revises: f3a9b4c2d5e8
Create Date: 2026-10-12

一次迁移做四件事（同一批治理能力，拆成多次迁移只会让升级过程更难回滚）：

1. `tenants` 新表 + 存量数据归入 default 租户（零回归的关键）；
2. 权限生命周期：`dataset_grants` 加 `expires_at` / `status` / `created_at`，
   新表 `grant_requests`（申请审批流）；
3. 数据质量巡检：`data_quality_rules` / `data_quality_results`；
4. 回答置信度：`agent_runs` 加 `confidence` / `confidence_reasons`；
   平台超管：`users` 加 `is_superadmin`。

租户列全部**可空**：NULL 表示"还没归属租户"，按 default 处理。
这样升级时不需要一条一条 UPDATE，也不需要停机。
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f3a9b4c2d5e8"
branch_labels = None
depends_on = None

DEFAULT_TENANT_SLUG = "default"


def upgrade() -> None:
    # ---- 1. 租户 ----
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # default 租户：存量数据全部归它，升级后行为与单租户时代一致
    op.execute(
        sa.text(
            "INSERT INTO tenants (name, slug, enabled) VALUES (:name, :slug, true) "
            "ON CONFLICT (slug) DO NOTHING"
        ).bindparams(name="默认组织", slug=DEFAULT_TENANT_SLUG)
    )

    # 各业务表加 tenant_id（可空 = 未归属，按 default 处理）。
    # 索引与 FK 都建：alembic check 会比对两者，漏一个就算 drift。
    for table in (
        "users",
        "datasets",
        "dataset_grants",
        "data_flows",
        "report_tasks",
        "monitor_tasks",
    ):
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.create_foreign_key(
            f"fk_{table}_tenant_id", table, "tenants", ["tenant_id"], ["id"]
        )
        # 存量行归 default 租户（NULL 也一视同仁，但显式写上更好排查）
        op.execute(
            sa.text(
                f"UPDATE {table} SET tenant_id = "
                "(SELECT id FROM tenants WHERE slug = :slug) "
                "WHERE tenant_id IS NULL"
            ).bindparams(slug=DEFAULT_TENANT_SLUG)
        )

    # ---- 2. 权限生命周期 ----
    op.add_column(
        "dataset_grants",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "dataset_grants",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dataset_grants",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # 状态/有效期列：模型未声明复合索引，这里也不建（多一个索引就是一处 drift）

    op.create_table(
        "grant_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("applicant_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("requested_row_rules", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("requested_field_whitelist", sa.JSON(), nullable=True),
        sa.Column("requested_days", sa.Integer(), nullable=True),
        sa.Column(
            "granted_id", sa.Integer(), sa.ForeignKey("dataset_grants.id"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_grant_requests_tenant_id", "grant_requests", ["tenant_id"])
    op.create_index(
        "ix_grant_requests_applicant_user_id", "grant_requests", ["applicant_user_id"]
    )
    op.create_index("ix_grant_requests_dataset_id", "grant_requests", ["dataset_id"])
    op.create_index("ix_grant_requests_status", "grant_requests", ["status"])
    op.create_index(
        "ix_grant_req_status_tenant", "grant_requests", ["status", "tenant_id"]
    )

    # ---- 3. 数据质量巡检 ----
    op.create_table(
        "data_quality_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("dataset_id", sa.Integer(), sa.ForeignKey("datasets.id"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rule_type", sa.String(16), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False, server_default="20.0"),
        sa.Column("target_field", sa.String(64), nullable=True),
        sa.Column("timestamp_field", sa.String(64), nullable=True),
        sa.Column("direction", sa.String(8), nullable=False, server_default="both"),
        sa.Column("cron", sa.String(64), nullable=False, server_default="0 7 * * *"),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(16), nullable=False, server_default=""),
    )
    # 索引名与模型保持一致（alembic check 按名比对）。
    # name 用 UniqueConstraint 而不是唯一索引：模型声明的是 unique=True，
    # alembic 比对的是"约束"，唯一索引会被算成 drift。
    op.create_unique_constraint("uq_data_quality_rules_name", "data_quality_rules", ["name"])
    op.create_index(
        "ix_data_quality_rules_tenant_id", "data_quality_rules", ["tenant_id"]
    )
    op.create_index(
        "ix_data_quality_rules_dataset_id", "data_quality_rules", ["dataset_id"]
    )
    op.create_index(
        "ix_dq_rule_tenant_dataset",
        "data_quality_rules",
        ["tenant_id", "dataset_id"],
    )

    op.create_table(
        "data_quality_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("data_quality_rules.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("baseline", sa.Float(), nullable=True),
        sa.Column("change_pct", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_data_quality_results_rule_id", "data_quality_results", ["rule_id"])
    op.create_index(
        "ix_dq_result_rule_time", "data_quality_results", ["rule_id", "checked_at"]
    )

    # ---- 4. 回答置信度 + 平台超管 ----
    op.add_column(
        "agent_runs", sa.Column("confidence", sa.String(16), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("confidence_reasons", sa.JSON(), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column(
            "is_superadmin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_superadmin")
    op.drop_column("agent_runs", "confidence_reasons")
    op.drop_column("agent_runs", "confidence")

    op.drop_index("ix_dq_result_rule_time", table_name="data_quality_results")
    op.drop_index("ix_data_quality_results_rule_id", table_name="data_quality_results")
    op.drop_table("data_quality_results")
    op.drop_index("ix_dq_rule_tenant_dataset", table_name="data_quality_rules")
    op.drop_index("ix_data_quality_rules_dataset_id", table_name="data_quality_rules")
    op.drop_index("ix_data_quality_rules_tenant_id", table_name="data_quality_rules")
    op.drop_constraint("uq_data_quality_rules_name", "data_quality_rules", type_="unique")
    op.drop_table("data_quality_rules")

    op.drop_index("ix_grant_req_status_tenant", table_name="grant_requests")
    op.drop_index("ix_grant_requests_status", table_name="grant_requests")
    op.drop_index("ix_grant_requests_dataset_id", table_name="grant_requests")
    op.drop_index("ix_grant_requests_applicant_user_id", table_name="grant_requests")
    op.drop_index("ix_grant_requests_tenant_id", table_name="grant_requests")
    op.drop_table("grant_requests")

    op.drop_column("dataset_grants", "created_at")
    op.drop_column("dataset_grants", "expires_at")
    op.drop_column("dataset_grants", "status")

    for table in (
        "monitor_tasks",
        "report_tasks",
        "data_flows",
        "dataset_grants",
        "datasets",
        "users",
    ):
        op.drop_constraint(f"fk_{table}_tenant_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_tenant_id", table_name=table)
        op.drop_column(table, "tenant_id")

    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
