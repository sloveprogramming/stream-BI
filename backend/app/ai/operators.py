"""批量 LLM 处理的通用管道：去重 → 查缓存 → 分批并发 → 回写缓存。

三条成本控制都在这里，AI 算子只管"怎么问模型"：
1. **去重**：同一段文本只处理一次（商品表里标题重复很常见）；
2. **缓存**：跨运行复用（落 `ai_llm_cache` 表，重启不丢）；
3. **分批并发**：批内一次多条降低调用次数，批间并发压缩墙钟时间。

本模块刻意不认识 flow —— 它只收一个 `per_batch` 回调，
这样单元测试可以直接塞假回调，不碰网络也不碰数据库。
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import AiLlmCache

# 回调签名：给一批输入，返回 {输入原文: 结果}；允许缺项（缺的会按失败处理）
PerBatch = Callable[[list[str]], dict[str, Any]]


def hash_input(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _dedupe(values: list[str]) -> list[str]:
    """保序去重（None 转空串，避免下游拼 prompt 时炸）。"""
    seen: dict[str, None] = {}
    for v in values:
        text = "" if v is None else str(v)
        seen.setdefault(text, None)
    return list(seen)


def _load_cache(session: Session | None, cache_key: str, hashes: list[str]) -> dict[str, Any]:
    if session is None or not hashes:
        return {}
    rows = (
        session.query(AiLlmCache)
        .filter(AiLlmCache.cache_key == cache_key, AiLlmCache.input_hash.in_(hashes))
        .all()
    )
    return {r.input_hash: r.output for r in rows}


def _save_cache(
    session: Session | None, cache_key: str, model: str | None, produced: dict[str, Any],
    value_by_hash: dict[str, str],
) -> None:
    if session is None or not produced:
        return
    existing = _load_cache(session, cache_key, list(produced))
    for input_hash, output in produced.items():
        if input_hash in existing:
            continue
        session.add(
            AiLlmCache(
                cache_key=cache_key,
                input_hash=input_hash,
                output=output,
                model=model,
            )
        )
    session.flush()


def run_llm_batch(
    *,
    values: list[str],
    cache_key: str,
    per_batch: PerBatch,
    batch_size: int = 20,
    max_concurrency: int = 4,
    session: Session | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """处理一批文本，返回 `{输入原文: 结果}`。

    `cache_key` 必须包含**所有会影响结果的参数**（提示词、标签集合、模型名…）：
    参数变了还复用旧缓存，就会静默产出错的标签。
    """
    unique = _dedupe(values)
    hash_by_value = {v: hash_input(v) for v in unique}
    value_by_hash = {h: v for v, h in hash_by_value.items()}

    cached = _load_cache(session, cache_key, list(hash_by_value.values()))

    missing = [v for v in unique if hash_by_value[v] not in cached]
    produced: dict[str, Any] = {}
    if missing:
        chunks = [missing[i : i + batch_size] for i in range(0, len(missing), batch_size)]
        workers = max(1, min(max_concurrency, len(chunks)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(per_batch, chunks):
                for value, output in (result or {}).items():
                    key = hash_by_value.get("" if value is None else str(value))
                    if key:
                        produced[key] = output
        _save_cache(session, cache_key, model, produced, value_by_hash)

    merged = {**cached, **produced}
    # 以"输入原文"为键返回，调用方不用关心哈希
    return {v: merged.get(hash_by_value[v]) for v in unique}


__all__ = ["PerBatch", "hash_input", "run_llm_batch"]
