"""问数用的提示词模板。

生产链路（`chat_api`）与评测链路（`eval`）共用这里，避免两处 prompt 各改各的、口径漂移。
"""


def system_prompt() -> str:
    return (
        "你是企业数据助手。根据给定的数据集结构生成一条只读 PostgreSQL SELECT。"
        "只能使用列出的表和字段；JOIN 键依据字段名和语义自行推理；只返回 SQL，不要解释。"
    )


def history_prompt(history: list) -> str:
    """把最近几轮问答带给模型，支持「那按省份呢」这类追问。"""
    if not history:
        return ""
    lines = ["", "", "最近的对话（用于理解追问，可复用其中的查询思路）："]
    for item in history[-3:]:
        q = (item or {}).get("question", "")
        prev_sql = (item or {}).get("sql", "")
        lines.append(f"- 问：{q}")
        if prev_sql:
            lines.append(f"  上次SQL：{prev_sql}")
    return "\n".join(lines)


def user_prompt(
    schema_prompt: str,
    question: str,
    history: list | None = None,
    examples: list | None = None,
) -> str:
    """组装给模型的完整用户消息（schema + 动态 few-shot + 历史 + 当前问题）。"""
    from app.assistant.fewshot import render as render_examples

    return (
        schema_prompt
        + render_examples(examples or [])
        + history_prompt(history or [])
        + "\n\n用户问题："
        + question
    )
