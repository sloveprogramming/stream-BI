"""主动监控：定时跑一个指标，按统计基线判异常，异常才调 Agent 归因。

设计取舍：**轮询用规则、归因才用 LLM**。
指标取值走一条只读 SQL（确定性、几乎零成本），只有真的发现异常了才花 Agent 的钱
去下钻归因 —— 这样「每天无人值守地跑」在经济上才成立。

推送默认**关闭**：`notify_webhook_url` 为空时只把简报存成 `agent_artifacts` 草稿，
由人看过再决定要不要发。填了 webhook 才真推送（等于显式授权）。
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MonitorTask(Base):
    __tablename__ = "monitor_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 用谁的身份取数：决定行级/字段级权限范围，与四个出口完全一致
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # 只读 SQL，必须返回单个数值（当前值）
    metric_sql: Mapped[str] = mapped_column(Text)
    # 异常时交给 Agent 的归因目标，可用 {name} {value} {reason} 占位
    investigate_goal: Mapped[str] = mapped_column(Text, default="")
    rule: Mapped[str] = mapped_column(String(16), default="pct_change")  # pct_change / zscore
    threshold: Mapped[float] = mapped_column(Float, default=20.0)
    direction: Mapped[str] = mapped_column(String(8), default="both")  # up / down / both
    window: Mapped[int] = mapped_column(Integer, default=14)  # 参与统计的历史点数
    cron: Mapped[str] = mapped_column(String(64), default="0 8 * * *")
    notify_webhook_url: Mapped[str] = mapped_column(String(512), default="")  # 空 = 只存草稿
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(16), default="")  # ok/anomaly/failed/skipped
    last_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class MonitorObservation(Base):
    __tablename__ = "monitor_observations"
    __table_args__ = (Index("ix_monitor_obs_task_time", "task_id", "observed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("monitor_tasks.id"))
    value: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
