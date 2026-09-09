from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SyncTask(Base):
    __tablename__ = "sync_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    source_table: Mapped[str] = mapped_column(String(200))
    target_table: Mapped[str] = mapped_column(String(200))  # 形如 ods.sales_order
    pk_columns: Mapped[list] = mapped_column(JSON)  # ["id"]
    columns: Mapped[list] = mapped_column(JSON)  # 同步字段清单（含主键与增量字段）
    incremental_field: Mapped[str | None] = mapped_column(String(100), nullable=True)  # None=全量比对
    cron: Mapped[str] = mapped_column(String(50))  # 5 段 cron
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncHistory(Base):
    __tablename__ = "sync_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("sync_tasks.id"))
    trigger: Mapped[str] = mapped_column(String(10))  # cron / manual
    status: Mapped[str] = mapped_column(String(10))  # success / failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rows_pulled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_written: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
