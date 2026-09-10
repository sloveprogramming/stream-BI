"""数据助手聊天接口：自然语言 → 只读 SQL → 权限重写 → 表格结果。"""
import json

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
    if not question:
        return StreamingResponse(
            _sse([{"type": "error", "message": "问题不能为空"}]),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _run(question, principal, ctx), media_type="text/event-stream"
    )


def _sse(events):
    for event in events:
        yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
    yield 'data: {"type":"done"}\n\n'


def _run(question: str, principal: AnyPrincipal, ctx: Context):
    try:
        schema = build_schema_context(
            ctx.session_factory, ctx.pg_engine, principal.user
        )
        if not schema["tables"]:
            yield from _sse([{"type": "error", "message": "当前账号没有可用数据集"}])
            return
        sql = generate_sql(
            _system_prompt(), schema["prompt"] + "\n\n用户问题：" + question
        )
        yield from _sse([{"type": "sql", "sql": sql}])
        validate_sql(sql, schema["tables"])
        rewritten, params = rewrite_sql(sql, schema["tables"])
        columns, rows = _execute(ctx, rewritten, params)
        yield from _sse(
            [
                {
                    "type": "result",
                    "columns": columns,
                    "rows": rows,
                    "row_count": len(rows),
                }
            ]
        )
    except SqlGuardError as exc:
        yield from _sse([{"type": "error", "message": str(exc)}])
    except Exception as exc:  # noqa: BLE001
        yield from _sse([{"type": "error", "message": f"查询失败：{exc}"[:300]}])


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
