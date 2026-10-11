"""文本向量化：**提供方抽象** + 环境探测。

为什么要这一层：向量能力有两种来源，它们的环境依赖完全不同 ——

- **本地模型**（BGE-M3）：要装 FlagEmbedding + torch（约 1GB 依赖）与 2.2GB 权重，
  本机无 GPU，CPU 推理慢，首次还要下载；好处是不花钱、数据不出机器；
- **HTTP API**（任何 OpenAI 兼容的 `/embeddings`）：只要一个 base_url 和 key；
  好处是立刻能用；代价是按量计费、文本出机器。

把选择权交给配置，而不是写死一种 —— 这两种在真实企业里都会用到
（敏感数据走本地，批量离线走 API）。

**绝不静默降级**：两条路都不可用时中止并给出两条出路。
如果这里"聪明地"退化成字符相似度，用户会以为自己在做语义去重，
拿到一批错的分组却毫无察觉。
"""
from __future__ import annotations

import importlib.util
import json
import threading
from dataclasses import dataclass

from app.ai.errors import AiError, AiUnavailableError
from app.settings import get_settings

PROVIDER_AUTO = "auto"
PROVIDER_API = "api"
PROVIDER_LOCAL = "local"
PROVIDER_NONE = "none"

# 本地模型的 import 名（权重另算，探测不加载权重）
_LOCAL_MODULE = "FlagEmbedding"

_FIX_HINT = (
    "向量能力需要二选一：\n"
    "  ① 配 HTTP API：在 backend/.env 里设 BISQL_EMBEDDING_API_BASE"
    "（可选 BISQL_EMBEDDING_API_KEY / BISQL_EMBEDDING_MODEL）；\n"
    f"  ② 装本地模型：pip install '{_LOCAL_MODULE}>=1.2'（连带 torch，约 1GB），"
    "并设 BISQL_EMBEDDING_PROVIDER=local。\n"
    "     权重约 2.2GB，首次运行会下载；国内先把 HF_ENDPOINT 指向镜像站"
    "（如 https://hf-mirror.com）。\n"
    "先跑 python scripts/check_embedding.py 看当前状态。"
)


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """探测结果。**不产生副作用**：不下载模型、不发起计费调用。"""

    provider: str  # 实际会用的："api" / "local" / "none"
    configured: str  # 配置里要求的值
    model: str
    reason: str  # 为什么选它 / 为什么用不了
    usable: bool
    fix_hint: str = ""


@dataclass(frozen=True, slots=True)
class EmbeddingOutcome:
    vectors: list[list[float]]
    provider: str
    model: str
    dim: int


# ---------------------------------------------------------------- 向量表示


def serialize_vector(values) -> str:
    """向量 → JSON 文本。

    保留 6 位小数：余弦比较到这个精度足够，体积比默认 repr 小一半左右。
    存文本而不是 pgvector 类型，是为了**不依赖数据库扩展**（本机 PG 没有 vector）。
    """
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"


def parse_vector(text) -> list[float]:
    """JSON 文本 → 浮点列表。给下游与测试用，保证往返一致。"""
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return [float(v) for v in text]
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise AiError(f"向量列不是合法 JSON：{str(text)[:80]!r}") from exc
    if not isinstance(data, list):
        raise AiError(f"向量列应为数组，实际是 {type(data).__name__}")
    return [float(v) for v in data]


def truncate_text(text) -> str:
    """按配置截断单条文本。超长文本既慢又几乎没有额外增益。"""
    limit = get_settings().embedding_max_chars
    value = "" if text is None else str(text)
    return value[:limit] if limit and limit > 0 else value


# ---------------------------------------------------------------- 探测


def local_available() -> tuple[bool, str]:
    """本机能不能跑本地模型。只查 import，**不加载权重**。"""
    try:
        spec = importlib.util.find_spec(_LOCAL_MODULE)
    except (ImportError, ValueError) as exc:  # 父包损坏时 find_spec 会抛
        return False, f"探测 {_LOCAL_MODULE} 失败（{type(exc).__name__}）"
    if spec is None:
        return False, f"未安装 {_LOCAL_MODULE}"
    return True, ""


def api_configured() -> bool:
    """配了 base_url 就算配好了 —— 本地自建的向量服务通常不需要 key。"""
    return bool((get_settings().embedding_api_base or "").strip())


def resolve_status() -> ProviderStatus:
    """按配置与环境的实际情况决定用哪个提供方。

    显式指定（`api` / `local`）时**不做任何回退**：用户说用哪个就用哪个，
    不可用就报错。含糊地"帮"他换一条路，是这类能力最危险的失败方式。
    """
    s = get_settings()
    choice = (s.embedding_provider or PROVIDER_AUTO).strip().lower()
    model = (s.embedding_model or "").strip()

    if choice == PROVIDER_NONE:
        return ProviderStatus(
            provider="none",
            configured=choice,
            model=model,
            reason="配置为 none（显式禁用向量能力）",
            usable=False,
            fix_hint=_FIX_HINT,
        )

    if choice == PROVIDER_API:
        if not api_configured():
            return ProviderStatus(
                "none", choice, model,
                "配置为 api，但没有设置 BISQL_EMBEDDING_API_BASE", False, _FIX_HINT,
            )
        return ProviderStatus(
            "api", choice, model,
            f"显式指定 API：{get_settings().embedding_api_base}", True,
        )

    if choice == PROVIDER_LOCAL:
        ok, why = local_available()
        if not ok:
            return ProviderStatus(
                "none", choice, model, f"配置为 local，但{why}", False, _FIX_HINT
            )
        return ProviderStatus("local", choice, model, f"显式指定本地模型：{model}", True)

    # auto：API 优先（立刻可用），其次本地
    if api_configured():
        return ProviderStatus(
            "api", choice, model,
            f"auto：检测到已配置 API 地址（{get_settings().embedding_api_base}）", True,
        )
    ok, why = local_available()
    if ok:
        return ProviderStatus("local", choice, model, "auto：本机可用本地模型", True)
    return ProviderStatus(
        "none", choice, model, f"auto：API 未配置，且本地不可用（{why}）", False, _FIX_HINT
    )


# ---------------------------------------------------------------- 调用


def embed_texts(texts: list[str], *, model: str = "", client=None) -> EmbeddingOutcome:
    """把一批文本转成向量。空输入直接返回空结果（不打服务）。"""
    values = [truncate_text(t) for t in texts]
    if not values:
        return EmbeddingOutcome(vectors=[], provider="", model=model, dim=0)

    status = resolve_status()
    if not status.usable:
        raise AiUnavailableError(f"向量化不可用：{status.reason}\n{status.fix_hint}")

    model_name = model or status.model
    if status.provider == "api":
        vectors = _embed_via_api(values, model=model_name, client=client)
    else:
        vectors = _embed_via_local(values, model=model_name)

    if len(vectors) != len(values):
        raise AiError(
            f"向量服务返回 {len(vectors)} 条，与输入 {len(values)} 条不符 —— 结果不可对齐，拒绝继续"
        )
    dim = len(vectors[0]) if vectors else 0
    if any(len(v) != dim for v in vectors):
        raise AiError("向量维度不一致，拒绝继续（下游按定长向量处理）")
    return EmbeddingOutcome(vectors=vectors, provider=status.provider, model=model_name, dim=dim)


def _build_api_client():
    from openai import OpenAI

    s = get_settings()
    return OpenAI(
        # 本地自建的向量服务多半不校验 key，但不能传空串给 SDK
        api_key=s.embedding_api_key or "not-needed",
        base_url=s.embedding_api_base,
        timeout=s.embedding_timeout_seconds,
    )


def _embed_via_api(values: list[str], *, model: str, client=None) -> list[list[float]]:
    s = get_settings()
    c = client or _build_api_client()
    out: list[list[float]] = []
    batch = max(1, s.embedding_batch_size)
    # 分批：单请求塞太多文本，有的服务会直接 413
    for start in range(0, len(values), batch):
        chunk = values[start : start + batch]
        resp = c.embeddings.create(model=model, input=chunk)
        out.extend([float(x) for x in item.embedding] for item in resp.data)
    return out


_MODEL_CACHE: dict[str, object] = {}
_MODEL_LOCK = threading.Lock()


def _load_local_model(model: str):
    """进程内只加载一次（加载一次几百 MB 到内存，重复加载会 OOM）。"""
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(model)
        if cached is None:
            from FlagEmbedding import BGEM3FlagModel  # 惰性：没装的人不该被牵连

            cached = BGEM3FlagModel(model, use_fp16=False)
            _MODEL_CACHE[model] = cached
        return cached


def _embed_via_local(values: list[str], *, model: str) -> list[list[float]]:
    encoder = _load_local_model(model)
    s = get_settings()
    result = encoder.encode(
        values,
        batch_size=max(1, s.embedding_batch_size),
        max_length=s.embedding_max_chars,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    dense = result["dense_vecs"]
    return [[float(x) for x in row] for row in dense]


__all__ = [
    "PROVIDER_API",
    "PROVIDER_AUTO",
    "PROVIDER_LOCAL",
    "PROVIDER_NONE",
    "EmbeddingOutcome",
    "ProviderStatus",
    "api_configured",
    "embed_texts",
    "local_available",
    "parse_vector",
    "resolve_status",
    "serialize_vector",
    "truncate_text",
]
