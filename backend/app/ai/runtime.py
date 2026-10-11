"""运行时模型配置：**平台默认配置优先，没有就回退环境变量**。

为什么叫 runtime：「用谁家的模型」由运行时的数据决定，而不是启动时的环境 ——
平台里点一下「设为默认」，下一个请求就生效，不用重启。

三级来源：

1. `model_providers` 里该用途（llm / embedding）**启用的默认配置**；
2. 没有默认 → 环境变量（`settings`），行为与引入本模块之前**完全一致**；
3. 环境变量也没有 → LLM 调用报「未配置」，embedding 走它自己的探测。

性能：每次解析多一次主键级查询。模型调用本身是秒级的网络往返，
这点开销可忽略；真要省，将来加个 TTL 缓存即可，接口不变。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.provider import KIND_LLM, ModelProvider
from app.security.secrets import decrypt_secret
from app.settings import get_settings

_factory = None


def _default_session_factory():
    """惰性建一个全局 session factory（消费方大多手头没有 session）。"""
    global _factory
    if _factory is None:
        from app.db import make_engine, make_session_factory

        s = get_settings()
        _factory = make_session_factory(make_engine(s.database_url))
    return _factory


@dataclass(frozen=True, slots=True)
class LlmRuntime:
    """一次 LLM 调用该用的地址、key 与模型。"""

    base_url: str
    api_key: str
    model: str
    planner_model: str
    source: str  # "db:<配置名>" | "env" —— 进日志，方便回答"刚才用的是谁"


@dataclass(frozen=True, slots=True)
class EmbeddingApiRuntime:
    """向量化 API 该用的地址、key 与模型（local 来源不走这里）。"""

    base_url: str
    api_key: str
    model: str
    source: str


def default_provider(kind: str, *, session=None) -> ModelProvider | None:
    """取某用途**启用的默认配置**；没有返回 None。

    并发下理论上可能出现两个默认（见 design 决策 5），
    取 id 更小的一条，保证结果确定。
    """
    own = session is None
    s = session if session is not None else _default_session_factory()()
    try:
        return (
            s.query(ModelProvider)
            .filter_by(kind=kind, enabled=True, is_default=True)
            .order_by(ModelProvider.id)
            .first()
        )
    finally:
        if own:
            s.close()


def resolve_llm(*, session=None) -> LlmRuntime:
    """问数 / Agent / AI 算子的 LLM 配置。"""
    s = get_settings()
    row = default_provider(KIND_LLM, session=session)
    if row is None:
        return LlmRuntime(
            base_url=s.llm_base_url,
            api_key=s.llm_api_key,
            model=s.llm_model,
            planner_model=s.llm_planner_model or s.llm_model,
            source="env",
        )
    # 本地服务（Ollama / vLLM）不填 key 是合法的
    api_key = "" if row.provider_kind == "local" else decrypt_secret(row.encrypted_api_key)
    return LlmRuntime(
        base_url=row.base_url,
        api_key=api_key,
        model=row.model,
        planner_model=row.planner_model or row.model,
        source=f"db:{row.name}",
    )


def resolve_embedding_api(*, session=None) -> EmbeddingApiRuntime | None:
    """向量化 API 配置。没有 DB 默认（或默认是 local 来源）返回 None，
    由 `embedding.resolve_status` 回退到环境变量逻辑。"""
    from app.models.provider import KIND_EMBEDDING

    row = default_provider(KIND_EMBEDDING, session=session)
    if row is None or row.provider_kind != "api":
        return None
    return EmbeddingApiRuntime(
        base_url=row.base_url,
        api_key=decrypt_secret(row.encrypted_api_key),
        model=row.model,
        source=f"db:{row.name}",
    )


def default_embedding_row(*, session=None) -> ModelProvider | None:
    """embedding 用途的默认配置行（含 local 来源），供探测逻辑判断。"""
    from app.models.provider import KIND_EMBEDDING

    return default_provider(KIND_EMBEDDING, session=session)


__all__ = [
    "EmbeddingApiRuntime",
    "LlmRuntime",
    "default_embedding_row",
    "default_provider",
    "resolve_embedding_api",
    "resolve_llm",
]
