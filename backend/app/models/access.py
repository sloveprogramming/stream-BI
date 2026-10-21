from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str | None] = mapped_column(String(128))
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"))
    # 归属租户（组织）。存量数据迁移到 default 租户，行为与单租户时代一致。
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # 平台超管：可跨租户操作（建租户、用 X-Tenant-Id 头切换）。与 is_admin 正交：
    # is_admin 管"这个租户内的管理权"，is_superadmin 管"跨租户"。
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(256))


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str | None] = mapped_column(String(128))
    source_table: Mapped[str] = mapped_column(String(128))
    pk_columns: Mapped[list] = mapped_column(JSON)
    columns: Mapped[list] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(String(256))
    # ERP 侧的中文列注释 {列名: 注释}，由 erp_autosync 回填；供问数时组装 schema 上下文
    column_comments: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 归属租户（组织）。租户内的数据集只对本租户用户可见。
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DatasetGrant(Base):
    __tablename__ = "dataset_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    # 归属租户：与 dataset / user 同租户的授权才可见（跨租户授权一律拒绝）
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )
    row_rules: Mapped[list] = mapped_column(JSON, default=list)
    field_whitelist: Mapped[list | None] = mapped_column(JSON)  # None=全部字段
    # 有效期与状态：到期/被撤销的授权不删数据，只改生效性（审计要能查到历史授权）
    # status: active（生效）/ expired（到期）/ revoked（被撤销）
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    key_id: Mapped[str] = mapped_column(String(32), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id"))
    channel: Mapped[str] = mapped_column(String(16))  # api/mcp/web/export
    dataset: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

class ReportTask(Base):
    __tablename__ = "report_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    format: Mapped[str] = mapped_column(String(8), default="xlsx")  # csv/xlsx
    cron: Mapped[str] = mapped_column(String(64), default="0 8 * * *")
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # 冗余 owner 的租户，便于按租户筛调度任务（owner 与 dataset 必须同租户）
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ReportRun(Base):
    __tablename__ = "report_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("report_tasks.id"))
    file_name: Mapped[str] = mapped_column(String(256))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="success")  # success/failed
    error: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
