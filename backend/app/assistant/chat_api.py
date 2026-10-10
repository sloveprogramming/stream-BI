"""数据助手聊天接口：自然语言 → 只读 SQL → 权限重写 → 表格结果。"""
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.assistant.llm import generate_sql
from app.assistant.prompts import system_prompt, user_prompt
from app.assistant.schema_context import build_schema_context
from app.assistant.tools import SqlGuardError, load_few_shots, run_scoped_sql

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
            system_prompt(),
            user_prompt(
                schema["prompt"],
                question,
                history,
                load_few_shots(ctx.session_factory, question, principal.user),
            ),
        )
        sql = sql.strip()
        while sql.endswith(";"):
            sql = sql[:-1].rstrip()
        yield from _sse([{"type": "sql", "sql": sql}])
        columns, rows = run_scoped_sql(
            ctx.pg_engine, sql, schema["tables"], ctx.settings.assistant_max_rows
        )
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
        recipe = _api_recipe(sql, schema["tables"])
        if recipe is not None:
            yield from _sse([{"type": "api", "api": recipe}])
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


def _api_recipe(sql: str, tables: list):
    """把生成的 SQL 反解成可直接调用的接口方案；失败不影响回答。"""
    from app.assistant.api_recipe import build_api_recipe

    try:
        return build_api_recipe(sql, tables)
    except Exception:  # noqa: BLE001
        return None
