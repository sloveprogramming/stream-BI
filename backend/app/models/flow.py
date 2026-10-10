"""数据编排（flow）元数据：流程定义、版本、运行记录。

设计要点（三条都关系到"能不能讲清楚这批数是怎么来的"）：

1. **版本化**：`DataFlowVersion` 存每一版 DSL 与编译产物，不覆盖历史，可回滚；
2. **owner 决定行权口径**：`DataFlow.owner_user_id` 是取数时套用的授权主体——
   编排产物本质是「owner 视角的物化结果」，所以产物默认也只有 owner 可见；
3. **grant_snapshot 记录实际行权**：同一条流程，不同权限的人跑出来内容不同。
   不记录当时用的是谁的行权，事后就说不清数据从哪来、为什么少了几个仓。
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_version_id() -> str:
    """版本标识：`flowv_` + 32 位十六进制，不暴露自增。"""
    return f"flowv_{uuid4().hex}"


def new_flow_run_id() -> str:
    """运行标识：`flowrun_` + 32 位十六进制。"""
    return f"flowrun_{uuid4().hex}"


class DataFlow(Base):
    """一条数据编排流程（对应画布上的一张图）。"""

    __tablename__ = "data_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(512))
    # 取数行权的归属主体：跑流程时按这个用户的授权裁剪源数据
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    target_schema: Mapped[str] = mapped_column(String(16), default="dwd")  # dwd/dws/ads
    target_table: Mapped[str] = mapped_column(String(128))  # 仅表名，不含 schema
    # 产物上架成的数据集（跑成功后回填，供四出口消费）
    output_dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"))
    cron: Mapped[str | None] = mapped_column(String(64))  # 空 = 仅手动触发
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )


class DataFlowVersion(Base):
    """流程的一个版本：DSL 原文 + 编译产物，两者都留档。"""

    __tablename__ = "data_flow_versions"
    __table_args__ = (UniqueConstraint("flow_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("data_flows.id"))
    version: Mapped[int] = mapped_column(Integer)
    version_id: Mapped[str] = mapped_column(String(40), unique=True)  # flowv_ + hex
    dsl: Mapped[dict] = mapped_column(JSON)  # 画布原始 DSL（回显 + 重新编译用）
    compiled_sql: Mapped[str | None] = mapped_column(Text)  # 编译出的 dbt model SQL
    compiled_yaml: Mapped[str | None] = mapped_column(Text)  # 编译出的 schema.yml（含 tests）
    dbt_selector: Mapped[str | None] = mapped_column(String(160))  # dbt --select 的选择器
    input_datasets: Mapped[list] = mapped_column(JSON, default=list)  # 血缘上游
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )


class FlowRun(Base):
    """一次运行记录。与 sync_history 同构，便于复用现有的同步监控看板。"""

    __tablename__ = "flow_runs"
    __table_args__ = (Index("ix_flow_runs_flow_started", "flow_id", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("data_flows.id"))
    version: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str] = mapped_column(String(40), unique=True)  # flowrun_ + hex
    triggered_by: Mapped[str] = mapped_column(String(16), default="manual")  # manual/cron/api
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/ok/failed/aborted
    rows_out: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    # ★ 本次运行实际套用的行级规则快照：{数据集名: [规则...]}
    grant_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 运行附加信息：产物数据集、无法继承的行权规则告警、dbt 输出摘要等
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trigger_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
