"""验证过的「问题 → SQL」对，用作动态 few-shot 的例子库。

为什么要独立成表：研究结论一致表明 —— 检索「最相似的历史验证样例」注入 prompt，
在留存问题上稳定优于静态示例集。这里就是那个语料库。

权限约束：`dataset_keys` 记录该样例用到哪些数据集；检索时**只返回用户可见数据集
能完全覆盖的样例**（子集判定）。否则 A 部门的历史问法/口径会顺着 prompt 漏给 B 部门。
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VerifiedQa(Base):
    __tablename__ = "verified_qa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 问题文本做唯一键：同一问法重复录入时覆盖，而不是堆一堆近似重复的样例
    question: Mapped[str] = mapped_column(Text, unique=True)
    sql: Mapped[str] = mapped_column(Text)
    dataset_keys: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual/confirmed/eval
    hits: Mapped[int] = mapped_column(Integer, default=0)  # 被检索命中次数，用于同分排序
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, server_default=func.now()
    )
