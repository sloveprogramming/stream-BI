"""问数评测结果记录。

评测用例本身以 JSON 文件为准（可 review、可 diff、可进版本库），
这里只落**每次评测的汇总**，用于看准确率趋势。

因此不再建 `eval_cases` 表 —— 同一份内容两处存放必然漂移。
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def new_batch_id() -> str:
    return f"eval_{uuid4().hex}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(40), unique=True, default=new_batch_id)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 用哪个账号的权限跑的
    sql_model: Mapped[str] = mapped_column(String(64), default="")
    planner_model: Mapped[str] = mapped_column(String(64), default="")
    total: Mapped[int] = mapped_column(Integer, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # by_status / by_tag / failures
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
