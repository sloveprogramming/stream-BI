"""AI 能力层的持久化：LLM 结果缓存。

为什么用表而不是内存字典：
- **跨进程、跨重启**：批量打标跑一次可能要几百次调用，重启就白烧钱；
- **可审计**：同一个输入对应哪个输出，出了问题能回查；
- **能被多个 Worker 共享**：将来交给 Dagster / 多进程跑时，缓存仍然有效。
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AiLlmCache(Base):
    """一次 LLM 调用的输入 → 输出映射。"""

    __tablename__ = "ai_llm_cache"
    __table_args__ = (UniqueConstraint("cache_key", "input_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 任务指纹：算子类型 + 提示词 + 标签集合等参数拼出来的标识。
    # 参数变了就不该复用旧结果，所以必须进 key。
    cache_key: Mapped[str] = mapped_column(String(200))
    # sha256(输入文本)：输入本身可能很长，用哈希做键
    input_hash: Mapped[str] = mapped_column(String(64))
    output: Mapped[dict] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
