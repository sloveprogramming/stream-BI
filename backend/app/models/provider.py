"""模型提供方：在平台内登记「用谁家的模型、key 是什么」。

与 `settings` 的关系：**DB 里的默认配置优先，没有就回退环境变量** ——
不配置任何东西时系统行为与引入本表之前完全一致（见 `app/ai/runtime.py`）。

安全：`encrypted_api_key` 是 Fernet 密文（见 `app/security/secrets.py`），
任何接口都只回掩码。key 留空对本地服务（Ollama / vLLM）是合法的。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

KIND_LLM = "llm"
KIND_EMBEDDING = "embedding"
KINDS = (KIND_LLM, KIND_EMBEDDING)

SOURCE_API = "api"
SOURCE_LOCAL = "local"
SOURCES = (SOURCE_API, SOURCE_LOCAL)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ModelProvider(Base):
    __tablename__ = "model_providers"
    # 解析器每次现查 (kind, enabled, is_default)，这个复合索引就是为它建的
    __table_args__ = (Index("ix_model_providers_kind_default", "kind", "is_default"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # llm | embedding —— 一个用途一条默认配置
    kind: Mapped[str] = mapped_column(String(16))
    # api | local —— local = 地址指向本机服务（Ollama / vLLM）或本地推理库
    provider_kind: Mapped[str] = mapped_column(String(16), default=SOURCE_API)
    base_url: Mapped[str] = mapped_column(String(255), default="")
    encrypted_api_key: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    # 仅 llm 用：规划/反思走推理模型；空 = 与 model 相同
    planner_model: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 同 kind 内唯一由应用层保证（set_default 时清同侧行）
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
