"""OpenAI 兼容客户端：自然语言 → SQL，以及 Agent 规划/反思的结构化调用。

模型路由（省钱又稳）：

| 角色      | 模型                        | 用途                     |
|-----------|-----------------------------|--------------------------|
| `sql`     | 配置的 `model`              | 生成 SQL，要快、要便宜    |
| `planner` | 配置的 `planner_model`      | 规划与反思，要稳、可容忍慢 |

配置来源见 `app/ai/runtime.py`：平台「模型配置」里的默认项优先，
没有则回退 `BISQL_LLM_*` 环境变量 —— 不在平台里配置时行为与从前一致。

约定：`generate_sql` 的行为与签名保持不变（已有测试与调用方依赖）。
"""
import json
import re

from openai import OpenAI

from app.ai.runtime import resolve_llm
from app.settings import get_settings

ROLE_SQL = "sql"
ROLE_PLANNER = "planner"


def build_client() -> OpenAI:
    cfg = resolve_llm()
    return OpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        timeout=get_settings().llm_timeout_seconds,
    )


def build_planner_client() -> OpenAI:
    """推理模型响应更慢，单独给更长的超时。"""
    cfg = resolve_llm()
    return OpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        timeout=get_settings().llm_planner_timeout_seconds,
    )


def model_for(role: str) -> str:
    cfg = resolve_llm()
    if role == ROLE_PLANNER:
        return cfg.planner_model or cfg.model
    return cfg.model


def _client_for(role: str, client=None) -> OpenAI:
    if client is not None:
        return client
    return build_planner_client() if role == ROLE_PLANNER else build_client()


def chat(
    system_prompt: str,
    user_prompt: str,
    *,
    role: str = ROLE_SQL,
    client=None,
    temperature: float = 0.0,
) -> str:
    """按角色路由模型的一次对话调用，返回纯文本。"""
    c = _client_for(role, client)
    resp = c.chat.completions.create(
        model=model_for(role),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()


def generate_sql(system_prompt: str, user_prompt: str, client=None) -> str:
    """调用大模型生成 SQL，并剥离 markdown 代码围栏。"""
    text = chat(system_prompt, user_prompt, role=ROLE_SQL, client=client)
    m = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.rstrip(";").strip()


def generate_json(
    system_prompt: str,
    user_prompt: str,
    *,
    role: str = ROLE_PLANNER,
    client=None,
    temperature: float = 0.0,
) -> dict:
    """要求模型输出 JSON 对象。解析失败返回 `{}`，由调用方兜底（不抛异常）。"""
    text = chat(
        system_prompt, user_prompt, role=role, client=client, temperature=temperature
    )
    return parse_json(text)


def parse_json(text: str) -> dict:
    """从模型回复里抠出 JSON 对象，兼容 ```json 围栏与前后废话。"""
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text or "", re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1) if fenced else (text or "")
    for piece in (candidate, _brace_slice(candidate)):
        if not piece:
            continue
        try:
            data = json.loads(piece)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def _brace_slice(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else ""
