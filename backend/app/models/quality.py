"""数据质量巡检：数据集级的日常体检。

## 与 `quality.assert` 算子的分工

| | `quality.assert`（编排算子） | 本模块（巡检） |
|---|---|---|
| 对象 | 一次编排**跑出来的结果** | 一个**数据集**本身 |
| 时机 | 流程运行时，逐行 | 定时，按 cron |
| 回答的问题 | "这批结果里哪些行违规" | "这批数今天怎么没来 / 怎么少了一半 / 这字段怎么全是空" |
| 命中后 | 打 `qa_passed=false` 标记，**不删行** | 落一条 anomaly 结果 + 记审计 |

两者互补，不合并：前者是研发期的自检，后者是运营期的值守。

## 刻意只做三类规则

- `freshness`：源表最近更新时间距现在超过阈值 → "数据没来"；
- `row_count`：行数环比涨跌幅超阈值 → "数据少了一半"；
- `null_rate`：指定字段空值率超阈值 → "字段全是空"。

这三类覆盖了中台被质疑的绝大多数场景。刻意不上时序模型
（与 `monitor/detect.py` 同一个取舍）：先验证"每天无人值守跑巡检"
这件事本身有没有价值。

## 安全

规则的 SQL 全部参数化；表名/字段名走 `access.scoped_query.check_ident`
（ASCII 标识符白名单），不接受任何拼接进来的用户输入。
"""
from datetime import datetime, timezone

from sqlalchemy import (
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

# 规则类型
RULE_FRESHNESS = "freshness"
RULE_ROW_COUNT = "row_count"
RULE_NULL_RATE = "null_rate"
RULE_TYPES = (RULE_FRESHNESS, RULE_ROW_COUNT, RULE_NULL_RATE)

# 巡检结果状态
RESULT_OK = "ok"
RESULT_ANOMALY = "anomaly"
RESULT_FAILED = "failed"
RESULT_SKIPPED = "skipped"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DataQualityRule(Base):
    """一条巡检规则：对某个数据集，按 cron 检查一项指标。"""

    __tablename__ = "data_quality_rules"
    __table_args__ = (Index("ix_dq_rule_tenant_dataset", "tenant_id", "dataset_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    rule_type: Mapped[str] = mapped_column(String(16))
    # 阈值语义随 rule_type 而变：
    #   freshness -> 小时数（超过即告警）
    #   row_count -> 百分比（环比涨跌幅超过即告警）
    #   null_rate -> 百分比（空值率超过即告警）
    threshold: Mapped[float] = mapped_column(Float, default=20.0)
    # null_rate 专用：检查哪个字段
    target_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # freshness 专用：用哪个字段判断新鲜度（通常是 update_time/modified）
    timestamp_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str] = mapped_column(String(8), default="both")  # up/down/both
    cron: Mapped[str] = mapped_column(String(64), default="0 7 * * *")

    # 用谁的身份取数：决定行级/字段级权限范围（与监控任务同一设计）
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str] = mapped_column(String(16), default="")


class DataQualityResult(Base):
    """一次巡检的结果。anomaly 也落库（不只报警）—— 历史要能查。"""

    __tablename__ = "data_quality_results"
    __table_args__ = (Index("ix_dq_result_rule_time", "rule_id", "checked_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("data_quality_rules.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    # 实测值。语义随 rule_type：
    #   freshness -> 距现在的小时数
    #   row_count -> 当前行数（绝对值，便于下期做环比）
    #   null_rate -> 空值率百分比
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 基线值：row_count 的上一期行数；其余规则为 None
    baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 环比变化百分比（仅 row_count 有值）：本期相对 baseline 的涨跌幅
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 人类可读的结论，例如 "行数环比 -62.50%（上期 1200 → 本期 450）"
    message: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
