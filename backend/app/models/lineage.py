"""血缘事件：每次编排成功运行落一条 OpenLineage RunEvent。

为什么存事件而不存"血缘图"：图可以随时从「流程输入 → 产物」的当前状态**重算**，
但"某次运行当时用了什么输入"只有事件能回答 —— 事件是审计与追溯的事实记录，
图是现状的视图（`app/flow/lineage.py` 负责重算）。

格式遵循 OpenLineage RunEvent（run / job / inputs / outputs / producer）：
将来接 Marquez、DataHub 或任何血缘工具，这批事件不用改。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

EVENT_COMPLETE = "COMPLETE"
NAMESPACE = "bisql"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LineageEvent(Base):
    __tablename__ = "lineage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # COMPLETE —— 目前只有编排成功一种；将来失败/中止事件也走这张表
    event_type: Mapped[str] = mapped_column(String(32))
    # job = 一次加工动作，命名 flow:<流程名>（与数据集词表区分开）
    job_name: Mapped[str] = mapped_column(String(255), index=True)
    # run = 本次运行（flow_runs.run_id），两边能对上
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    flow_id: Mapped[int] = mapped_column(Integer, index=True)
    # OpenLineage RunEvent 全文 —— 标准格式的价值就在这列
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
