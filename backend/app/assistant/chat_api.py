"""数据助手聊天接口：自然语言 → 只读 SQL → 权限重写 → 表格结果。"""
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.assistant.llm import generate_sql
from app.assistant.schema_context import build_schema_context
from app.assistant.sql_guard import SqlGuardError, validate_sql
from app.assistant.sql_rewrite import rewrite_sql

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post("/chat")
def chat(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    question = (body.get("question") or "").strip()
    history = body.get("history") or []
    session_id = (body.get("session_id") or "").strip()[:64] or None
    if not question:
        return StreamingResponse(
            _sse([{"type": "error", "message": "问题不能为空"}]),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _run(question, history, session_id, principal, ctx),
        media_type="text/event-stream",
    )


def _sse(events):
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
    yield 'data: {"type":"done"}\n\n'


def _run(question: str, history: list, session_id: str | None, principal: AnyPrincipal, ctx: Context):
    started = time.perf_counter()
    sql = ""
    row_count = 0
    error: str | None = None
    try:
        schema = build_schema_context(
            ctx.session_factory, ctx.pg_engine, principal.user
        )
        if not schema["tables"]:
            error = "当前账号没有可用数据集"
            yield from _sse([{"type": "error", "message": error}])
            return
        sql = generate_sql(
            _system_prompt(),
            schema["prompt"] + _history_prompt(history) + "\n\n用户问题：" + question,
        )
        sql = sql.strip()
        while sql.endswith(";"):
            sql = sql[:-1].rstrip()
        yield from _sse([{"type": "sql", "sql": sql}])
        validate_sql(sql, schema["tables"])
        rewritten, params = rewrite_sql(sql, schema["tables"])
        columns, rows = _execute(ctx, rewritten, params)
        row_count = len(rows)
        yield from _sse(
            [
                {
                    "type": "result",
                    "columns": columns,
                    "rows": rows,
                    "row_count": row_count,
                }
            ]
        )
    except SqlGuardError as exc:
        error = str(exc)
        yield from _sse([{"type": "error", "message": error}])
    except Exception as exc:  # noqa: BLE001
        error = f"查询失败：{exc}"[:300]
        yield from _sse([{"type": "error", "message": error}])
    finally:
        _audit(ctx, principal, session_id, question, sql, row_count, started, error)


def _audit(ctx, principal, session_id, question, sql, row_count, started, error):
    """按会话 id 记录一次问答，便于按聊天记录追溯。"""
    from app.models import AuditLog

    try:
        with ctx.session_factory() as session:
            session.add(
                AuditLog(
                    user_id=principal.user.id,
                    api_key_id=getattr(principal.api_key, "id", None),
                    channel="assistant",
                    dataset=None,
                    action="chat",
                    row_count=row_count,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    detail={
                        "session_id": session_id,
                        "question": question,
                        "sql": sql,
                        "error": error,
                    },
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001 —— 审计失败不影响回答
        pass


def _execute(ctx: Context, sql: str, params: dict):
    max_rows = ctx.settings.assistant_max_rows
    with ctx.pg_engine.begin() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
        result = conn.execute(text(sql), params)
        columns = list(result.keys())
        rows = [list(row) for row in result.fetchmany(max_rows + 1)]
    return columns, rows[:max_rows]


def _system_prompt() -> str:
    return (
        "你是企业数据助手。根据给定的数据集结构生成一条只读 PostgreSQL SELECT。"
        "只能使用列出的表和字段；JOIN 键依据字段名和语义自行推理；只返回 SQL，不要解释。"
    )


def _history_prompt(history: list) -> str:
    """把最近几轮问答带给模型，支持“那按省份呢”这类追问。"""
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
