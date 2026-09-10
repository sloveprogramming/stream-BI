from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
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
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
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
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class DatasetGrant(Base):
    __tablename__ = "dataset_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    row_rules: Mapped[list] = mapped_column(JSON, default=list)
    field_whitelist: Mapped[list | None] = mapped_column(JSON)  # None=全部字段


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
