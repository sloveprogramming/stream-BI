"""LLM 结构化输出：把「模型自由发挥的文本」变成「能直接用的对象」。

复用 `app/assistant/llm.py` 的客户端与模型路由，**不重写一套 provider** ——
只加一件这里最需要的事：用 Pydantic 模型约束输出，解析失败把错误喂回去重试。

为什么必须结构化：AI 算子的产出要写进表里（一列标签、几个抽取字段），
自由文本没法落库；而偶尔的格式漂移如果靠正则去猜，迟早出错。
"""
from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.errors import AiUnavailableError, StructuredOutputError
from app.assistant.llm import ROLE_SQL, chat, parse_json
from app.settings import get_settings

T = TypeVar("T", bound=BaseModel)

_DEFAULT_SYSTEM = (
    "你是一个数据加工助手，只做机械的标注/抽取工作，不做解释、不做创作。"
    "必须严格按用户给定的 JSON 结构输出，不要输出任何多余文字。"
)


def ensure_llm_ready() -> None:
    """没配 key 就直接给出可操作的错误，别等到调 API 才报 401。"""
    if not get_settings().llm_api_key:
        raise AiUnavailableError(
            "未配置 BISQL_LLM_API_KEY，AI 算子无法调用模型。"
            "请在 backend/.env 里补上后重试。"
        )


def _schema_hint(schema: type[BaseModel]) -> str:
    return (
        "\n\n只输出一个 JSON 对象（不要 markdown 围栏、不要解释），结构为：\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )


def structured_call(
    prompt: str,
    schema: type[T],
    *,
    system: str = "",
    role: str = ROLE_SQL,
    client=None,
    retries: int = 2,
) -> T:
    """调模型并强制其输出符合 `schema`。

    解析或校验失败时，把**具体错误**回灌给模型重试（有界），
    比"重试同样的话"有效得多 —— 模型能看到自己哪里不合规。
    """
    ensure_llm_ready()
    hint = _schema_hint(schema)
    attempt_prompt = prompt
    last_error: Exception | None = None

    for _ in range(retries + 1):
        text = chat(system or _DEFAULT_SYSTEM, attempt_prompt + hint, role=role, client=client)
        data = parse_json(text)
        if data:
            try:
                return schema.model_validate(data)
            except ValidationError as exc:
                last_error = exc
                attempt_prompt = (
                    f"{prompt}\n\n上次输出不合法，校验错误如下：\n{exc}\n"
                    f"请修正后严格按给定结构重新输出。"
                )
                continue
        last_error = StructuredOutputError(f"模型未返回可解析的 JSON：{text[:300]!r}")
        attempt_prompt = (
            f"{prompt}\n\n上次输出不是合法 JSON：{text[:300]!r}\n"
            f"请只输出一个 JSON 对象，不要任何其他文字。"
        )

    raise StructuredOutputError(f"模型连续 {retries + 1} 次未按结构输出：{last_error}")
