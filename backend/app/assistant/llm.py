"""DeepSeek（OpenAI 兼容）客户端：把自然语言问题转成 SQL。"""
import re

from openai import OpenAI

from app.settings import get_settings


def build_client() -> OpenAI:
    s = get_settings()
    return OpenAI(
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        timeout=s.llm_timeout_seconds,
    )


def generate_sql(system_prompt: str, user_prompt: str, client=None) -> str:
    """调用大模型生成 SQL，并剥离 markdown 代码围栏。"""
    c = client or build_client()
    s = get_settings()
    resp = c.chat.completions.create(
        model=s.llm_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )
    text = (resp.choices[0].message.content or "").strip()
    m = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.rstrip(";").strip()
