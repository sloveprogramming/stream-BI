"""Agent 问数接口：给一个业务目标，Agent 自己拆解、多步取数、给出带依据的结论。

与 `/assistant/chat`（单轮 Text-to-SQL）并存，由 `BISQL_AGENT_ENABLED` 控制开关；
未启用时返回明确错误，不改动既有链路的行为。

SSE 事件：
    run      —— 本轮运行号
    plan     —— 拆出的子问题清单
    step     —— 每个节点（plan/act/reflect/answer）的完成情况
    answer   —— 最终结论 + 每个子问题的 SQL 与行数
    error    —— 失败原因
    done     —— 结束标记
"""
import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.assistant.agent import (
    AgentContext,
    AgentResult,
    save_result,
    stream_agent,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _sse(events):
    """仅在一次性响应里用：逐条发事件后补一个 done 结束标记。"""
    for event in events:
        yield _frame(event)
    yield _frame({"type": "done"})


def _agent_context(ctx: Context, principal: AnyPrincipal) -> AgentContext:
    settings = ctx.settings
    return AgentContext(
        session_factory=ctx.session_factory,
        pg_engine=ctx.pg_engine,
        user=principal.user,
        max_rows=settings.assistant_max_rows,
        evidence_rows=settings.agent_evidence_rows,
        max_sub_questions=settings.agent_max_sub_questions,
        max_replans=settings.agent_max_replans,
        max_tool_calls=settings.agent_max_tool_calls,
        max_sql_attempts=settings.agent_max_sql_attempts,
        clarify_enabled=settings.agent_clarify_enabled,
    )


def check_request(goal: str, settings) -> str | None:
    """请求校验：返回错误信息，或 None 表示通过。抽成纯函数便于单测。"""
    if not settings.agent_enabled:
        return "Agent 模式未启用（BISQL_AGENT_ENABLED=false）"
    if not goal:
        return "目标不能为空"
    return None


@router.post("/ask")
def ask(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    goal = (body.get("goal") or body.get("question") or "").strip()
    error = check_request(goal, ctx.settings)
    if error is not None:
        return StreamingResponse(
            _sse([{"type": "error", "message": error}]), media_type="text/event-stream"
        )
    return StreamingResponse(_run(goal, principal, ctx), media_type="text/event-stream")


def _run(goal: str, principal: AnyPrincipal, ctx: Context):
    from app.models import new_run_id

    agent_ctx = _agent_context(ctx, principal)
    run_id = new_run_id()
    started = time.perf_counter()
    yield _frame({"type": "run", "run_id": run_id, "goal": goal})

    final: dict = {}
    status = "ok"
    try:
        for node, update in stream_agent(goal, agent_ctx):
            if node == "__final__":
                final = update
                continue
            if node == "plan":
                yield _frame({
                    "type": "plan",
                    "sub_questions": [s["question"] for s in (update.get("plan") or [])],
                })
            step = (update.get("steps") or [{}])[-1]
            yield _frame({
                "type": "step",
                "node": node,
                "status": step.get("status", "ok"),
                "tool": step.get("tool"),
                "elapsed_ms": step.get("elapsed_ms", 0),
            })
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        yield _frame({"type": "error", "message": f"Agent 运行失败：{exc}"[:300]})

    answer = (final.get("answer") or "").strip()
    clarification = final.get("clarification") or {}
    if clarification and status == "ok":
        status = "needs_clarification"
        answer = answer or str(clarification.get("question") or "")
    yield _frame({
        "type": "answer",
        "answer": answer,
        "status": status,
        "clarification": clarification,
        "plan": [
            {
                "question": s.get("question"),
                "status": s.get("status"),
                "sql": s.get("sql"),
                "row_count": s.get("row_count", 0),
                "error": s.get("error", ""),
            }
            for s in (final.get("plan") or [])
        ],
        "tool_calls": final.get("tool_calls", 0),
    })
    save_result(
        AgentResult(
            run_id=run_id,
            status=status,
            answer=answer,
            plan=final.get("plan") or [],
            steps=final.get("steps") or [],
            tool_calls=final.get("tool_calls", 0),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        ),
        agent_ctx,
        goal,
    )
    yield _frame({"type": "done"})
