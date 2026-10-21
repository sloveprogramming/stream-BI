"""Agent 循环：Plan-and-Execute + 反思，跑在 LangGraph 上。

与 `/assistant/chat` 单轮问数的区别：

    单轮：  问题  →  一条 SQL  →  一张表
    Agent： 目标  →  自己拆子问题  →  逐个取数  →  自检证据是否充分  →  不够就继续  →  带依据的结论

安全边界完全不变：每一步取数都走 `tools.run_scoped_sql`
（sqlglot 安全闸门 → 权限重写 → 只读事务 + 超时 + 行数上限），
LLM 始终没有直连数据库的机会。
"""
from __future__ import annotations

import operator
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from app.assistant import tools
from app.assistant.llm import ROLE_PLANNER, chat, generate_json, generate_sql
from app.assistant.prompts import system_prompt, user_prompt
from app.assistant.tools import run_scoped_sql
from app.assistant.verify import query_with_retry
from app.metrics import ambiguous_metrics, clarification_for

PLAN_SYSTEM = (
    "你是企业数据分析的规划助手。把给定的业务问题拆成 2~5 个可由 SQL 逐步回答的子问题，"
    "体现从整体到细节的递进；子问题要具体、能独立查询。"
    '只输出 JSON，形如 {"sub_questions": ["子问题1", "子问题2"]}，不要任何解释。'
    "如果问题本身很简单，只给 1 个子问题。"
)

REFLECT_SYSTEM = (
    "你是数据分析的证据审查员。根据目标与已取得的查询证据，判断证据是否足以回答目标。"
    '只输出 JSON，形如 {"enough": true, "reason": "说明"}。'
    "证据为空、报错、或与目标无关时，enough 必须为 false。"
)

ANSWER_SYSTEM = (
    "你是企业经营分析助手。只依据给定的查询证据作答，不要编造数字。"
    "先给结论，再给支撑数据；证据不足就明确说明缺什么。用简洁的中文分点回答。"
)

CLARIFY_SYSTEM = (
    "你是需求澄清助手。判断用户的问题是否缺少必要口径或范围，导致无法唯一确定查询方式。"
    '能合理默认的就不要追问，输出 {"ambiguous": false}；'
    "只有不同理解会导致完全不同的查询结果时才追问，输出 "
    '{"ambiguous": true, "question": "要向用户确认的问题", "options": ["一种理解", "另一种理解"]}。'
    "只输出 JSON，不要解释。"
)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _metric_hit(agent_ctx, question: str) -> bool:
    """问题里有没有一个词能命中指标字典。

    这是"口径有依据"的确定性信号：命中了说明模型拿到的是字典里的公式，
    没命中说明它按字面理解自己编了口径 —— 后者要降置信度。
    字典加载失败时返回 False（宁可真话少说，不给虚假的高置信度）。
    """
    try:
        return bool(agent_ctx.metrics().search(question))
    except Exception:  # noqa: BLE001 —— 字典不可用不该让问数失败
        return False


# ---------------------------------------------------------------- 依赖注入

@dataclass
class LlmAdapter:
    """把 LLM 调用收敛成三个方法，测试时注入假实现即可，不必打桩到具体库函数。"""

    def sql(self, system: str, user: str) -> str:
        return generate_sql(system, user)

    def json(self, system: str, user: str) -> dict:
        return generate_json(system, user, role=ROLE_PLANNER)

    def text(self, system: str, user: str) -> str:
        return chat(system, user, role=ROLE_PLANNER)


@dataclass
class AgentContext:
    session_factory: Any
    pg_engine: Any
    user: Any
    max_rows: int = 1000
    evidence_rows: int = 20
    max_sub_questions: int = 5
    max_replans: int = 1
    max_tool_calls: int = 12
    max_sql_attempts: int = 2
    clarify_enabled: bool = True
    # 指标字典：注入了就用注入的（测试友好），否则按需从文件加载
    metrics_dictionary: Any = field(default=None, repr=False)
    _schema: dict | None = field(default=None, repr=False)
    _metrics: Any = field(default=None, repr=False)

    def schema(self) -> dict:
        if self._schema is None:
            self._schema = tools.load_schema(self.session_factory, self.pg_engine, self.user)
        return self._schema

    def metrics(self):
        """指标字典。口径判定的依据，与编排链路读的是同一份。"""
        if self.metrics_dictionary is not None:
            return self.metrics_dictionary
        if self._metrics is None:
            from app.metrics import load_for

            with self.session_factory() as session:
                self._metrics = load_for(session)
        return self._metrics


class AgentState(TypedDict, total=False):
    goal: str
    plan: list
    steps: Annotated[list, operator.add]
    replans: int
    tool_calls: int
    verdict: dict
    clarification: dict
    next: str
    answer: str


# ---------------------------------------------------------------- 轨迹

def _trace(node: str, tool: str | None, status: str, step_input: dict, output: dict, started: float) -> dict:
    return {
        "node": node,
        "tool": tool,
        "status": status,
        "step_input": step_input,
        "output": output,
        "elapsed_ms": _ms(started),
    }


def _update_plan(plan: list, step_id: int, **changes) -> list:
    return [{**s, **changes} if s["id"] == step_id else dict(s) for s in plan]


def _pending(plan: list) -> list:
    return [s for s in plan if s.get("status") == "pending"]


def _evidence_lines(state: AgentState) -> str:
    lines = []
    for step in state.get("plan") or []:
        if step.get("status") == "done":
            lines.append(f"【子问题】{step['question']}")
            lines.append(f"【SQL】{step.get('sql', '')}")
            lines.append(f"【结果列】{', '.join(step.get('columns') or [])}")
            for row in (step.get("rows") or [])[:5]:
                lines.append(f"  数据：{row}")
            if step.get("row_count", 0) > len(step.get("rows") or []):
                lines.append(f"  （共 {step['row_count']} 行，仅展示前 {len(step.get('rows') or [])} 行）")
        elif step.get("status") == "failed":
            lines.append(f"【子问题】{step['question']} —— 查询失败：{step.get('error', '')}")
    return "\n".join(lines) if lines else "（暂无有效证据）"


# ---------------------------------------------------------------- 图

def build_graph(agent_ctx: AgentContext, llm: LlmAdapter | None = None):
    from langgraph.graph import END, START, StateGraph

    llm = llm or LlmAdapter()

    def clarify_node(state: AgentState) -> dict:
        """口径不明就先反问，而不是猜一个口径直接查。

        分两层，顺序不能反：

        ① **口径歧义由指标字典判定**（`app/metrics/ambiguity.py`）——
           确定性、可解释、可单测。这一步刻意不交给模型：让模型自行判断该用哪个口径，
           它正是会自信地给出一个错的数；
        ② 剩下"缺少范围/时间条件"这类问题才交给模型判断。

        刻意做成**无状态**：直接把澄清问题作为本轮结论返回，由客户端追问。
        真 `interrupt()` 需要 checkpointer + thread_id + resume 接口，
        收益不足以抵消这套机器的复杂度；将来若要做多轮挂起再升级。
        """
        started = time.perf_counter()
        if not agent_ctx.clarify_enabled:
            return {"next": "plan"}

        hits = ambiguous_metrics(agent_ctx.metrics(), state["goal"])
        if hits:
            return {
                "clarification": clarification_for(hits),
                "next": "end",
                "steps": [
                    _trace(
                        "clarify",
                        "metric_dictionary",
                        "ok",
                        {"goal": state["goal"]},
                        {"ambiguous": True, "metrics": [m.name for m in hits]},
                        started,
                    )
                ],
            }

        verdict = llm.json(CLARIFY_SYSTEM, f"用户问题：{state['goal']}")
        if not verdict.get("ambiguous"):
            return {
                "next": "plan",
                "steps": [_trace("clarify", "llm", "ok", {}, {"ambiguous": False}, started)],
            }
        return {
            "clarification": {
                "question": str(verdict.get("question") or "请补充一下口径或范围。"),
                "options": [str(o) for o in (verdict.get("options") or [])][:4],
            },
            "next": "end",
            "steps": [_trace("clarify", "llm", "ok", {}, {"ambiguous": True}, started)],
        }

    def plan_node(state: AgentState) -> dict:
        started = time.perf_counter()
        schema = agent_ctx.schema()
        user_msg = (
            f"可用数据集：\n{schema['prompt']}\n\n业务问题：{state['goal']}"
        )
        resp = llm.json(PLAN_SYSTEM, user_msg)
        subs = [str(s).strip() for s in (resp.get("sub_questions") or []) if str(s).strip()]
        if not subs:  # 规划失败也要能退化跑通：把原问题当成唯一的子问题
            subs = [state["goal"]]
        subs = subs[: agent_ctx.max_sub_questions]
        plan = [
            {
                "id": i + 1,
                "question": q,
                "status": "pending",
                "sql": "",
                "columns": [],
                "rows": [],
                "row_count": 0,
                "error": "",
            }
            for i, q in enumerate(subs)
        ]
        return {
            "plan": plan,
            "verdict": {},
            "steps": [_trace("plan", "llm", "ok", {"goal": state["goal"]}, {"sub_questions": subs}, started)],
        }

    def act_node(state: AgentState) -> dict:
        started = time.perf_counter()
        waiting = _pending(state.get("plan") or [])
        if not waiting:
            return {}
        target = waiting[0]
        schema = agent_ctx.schema()

        def generate(feedback: str) -> str:
            examples = tools.load_few_shots(
                agent_ctx.session_factory, target["question"], agent_ctx.user
            )
            # 记录"这次生成有没有用到人工样例" —— 置信度判定要读（见 confidence.py）。
            # 记在闭包的非局部变量上，随本次 act 一起回传。
            found["fewshot"] = bool(examples)
            prompt = user_prompt(
                schema["prompt"],
                target["question"],
                None,
                examples,
                schema.get("metrics", ""),
            )
            return llm.sql(system_prompt(), prompt + feedback)

        def execute(sql: str):
            return run_scoped_sql(
                agent_ctx.pg_engine, sql, schema["tables"], agent_ctx.max_rows
            )

        # 生成 → 执行 → 体检 → 不合格就把原因喂回去改写（有界），见 verify 模块
        found: dict = {"fewshot": False, "metric": False}
        attempt = query_with_retry(
            generate, execute, max_attempts=agent_ctx.max_sql_attempts
        )
        # 口径是否命中指标字典：用"问题里的词能在字典里检索到"判定（确定性，不交给模型）
        found["metric"] = _metric_hit(agent_ctx, target["question"])
        plan = _update_plan(
            state["plan"],
            target["id"],
            status="done" if attempt.ok else "failed",
            sql=attempt.sql,
            columns=attempt.columns,
            rows=[list(row) for row in attempt.rows[: agent_ctx.evidence_rows]],
            row_count=len(attempt.rows),
            error="" if attempt.ok else attempt.problem,
            # 置信度判定要读这两个：改写了几次才通过、口径/样例有没有依据
            attempts=attempt.attempts,
            fewshot_hit=found["fewshot"],
            metric_hit=found["metric"],
        )
        return {
            "plan": plan,
            "tool_calls": state.get("tool_calls", 0) + max(attempt.attempts, 1),
            "steps": [
                _trace(
                    "act",
                    "run_sql",
                    attempt.status,
                    {"step_id": target["id"], "question": target["question"], "sql": attempt.sql},
                    {
                        "columns": attempt.columns,
                        "row_count": len(attempt.rows),
                        "attempts": attempt.attempts,
                        "problem": attempt.problem,
                    },
                    started,
                )
            ],
        }

    def reflect_node(state: AgentState) -> dict:
        started = time.perf_counter()
        waiting = _pending(state.get("plan") or [])
        calls = state.get("tool_calls", 0)
        if waiting and calls < agent_ctx.max_tool_calls:
            return {
                "next": "act",
                "steps": [
                    _trace("reflect", None, "ok", {"pending": len(waiting)}, {"next": "act"}, started)
                ],
            }
        verdict = llm.json(
            REFLECT_SYSTEM,
            f"目标：{state['goal']}\n\n已取得证据：\n{_evidence_lines(state)}",
        )
        enough = bool(verdict.get("enough", True))
        can_replan = state.get("replans", 0) < agent_ctx.max_replans and calls < agent_ctx.max_tool_calls
        if not enough and can_replan:
            nxt, replans = "plan", state.get("replans", 0) + 1
        else:
            nxt, replans = "answer", state.get("replans", 0)
        return {
            "verdict": verdict,
            "next": nxt,
            "replans": replans,
            "steps": [
                _trace(
                    "reflect", "llm", "ok",
                    {"enough": enough}, {"next": nxt, "reason": verdict.get("reason", "")}, started,
                )
            ],
        }

    def answer_node(state: AgentState) -> dict:
        started = time.perf_counter()
        text = llm.text(
            ANSWER_SYSTEM,
            f"业务问题：{state['goal']}\n\n查询证据：\n{_evidence_lines(state)}",
        )
        return {
            "answer": text,
            "steps": [_trace("answer", "llm", "ok", {}, {"answer": text}, started)],
        }

    graph = StateGraph(AgentState)
    graph.add_node("clarify", clarify_node)
    graph.add_node("plan", plan_node)
    graph.add_node("act", act_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("answer", answer_node)
    graph.add_edge(START, "clarify")
    graph.add_conditional_edges(
        "clarify",
        lambda state: state.get("next", "plan"),
        {"plan": "plan", "end": END},
    )
    graph.add_edge("plan", "act")
    graph.add_edge("act", "reflect")
    graph.add_conditional_edges(
        "reflect",
        lambda state: state.get("next", "answer"),
        {"act": "act", "plan": "plan", "answer": "answer"},
    )
    graph.add_edge("answer", END)
    return graph.compile()


# ---------------------------------------------------------------- 入口

@dataclass
class AgentResult:
    run_id: str
    status: str
    answer: str = ""
    plan: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    tool_calls: int = 0
    elapsed_ms: int = 0
    error: str = ""
    clarification: dict = field(default_factory=dict)
    # 回答置信度：high/medium/low + 机器可读的 reasons（见 confidence.py）
    confidence: str = ""
    confidence_reasons: list = field(default_factory=list)


def _merge(target: dict, update: dict) -> None:
    """把节点更新合并进总状态；`steps` 是累加语义（与 StateGraph 的 reducer 一致）。"""
    for key, value in (update or {}).items():
        if key == "steps":
            target["steps"] = list(target.get("steps") or []) + list(value or [])
        else:
            target[key] = value


def stream_agent(goal: str, agent_ctx: AgentContext, llm: LlmAdapter | None = None):
    """逐步执行 Agent，产出 `(node_name, node_update)`；结束时产出 `("__final__", 总状态)`。

    用 LangGraph 的 `stream_mode="updates"` 拿到真实的逐步执行，
    而不是跑完再回放 —— SSE 才有意义。
    """
    graph = build_graph(agent_ctx, llm)
    initial: AgentState = {"goal": goal, "plan": [], "steps": [], "replans": 0, "tool_calls": 0}
    final: dict = dict(initial)
    for chunk in graph.stream(initial, {"recursion_limit": 80}, stream_mode="updates"):
        for node, update in (chunk or {}).items():
            _merge(final, update)
            yield node, update
    yield "__final__", final


def run_agent(goal: str, agent_ctx: AgentContext, llm: LlmAdapter | None = None,
              persist: bool = True) -> AgentResult:
    """跑一轮 Agent，并把运行轨迹落库（`agent_runs` / `agent_steps`）。"""
    from app.models import new_run_id

    run_id = new_run_id()
    started = time.perf_counter()
    final: dict = {}
    status, error = "ok", ""
    try:
        for node, update in stream_agent(goal, agent_ctx, llm):
            if node == "__final__":
                final = update
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", str(exc)[:500]

    clarification = final.get("clarification") or {}
    answer = (final.get("answer") or "").strip()
    if clarification and status == "ok":
        status = "needs_clarification"
        answer = answer or str(clarification.get("question") or "")

    plan = final.get("plan") or []
    conf = _confidence_for(plan, final)
    result = AgentResult(
        run_id=run_id,
        status=status,
        answer=answer,
        plan=plan,
        steps=final.get("steps") or [],
        tool_calls=final.get("tool_calls", 0),
        elapsed_ms=_ms(started),
        error=error,
        clarification=clarification,
        confidence=conf.level,
        confidence_reasons=list(conf.reasons),
    )
    if persist:
        save_result(result, agent_ctx, goal)
    return result


def _confidence_for(plan: list, final: dict):
    """从 plan 采集信号并判定置信度。口径/样例命中按"任一子问题命中"算。"""
    from app.assistant.confidence import assess, collect_signals_from_plan

    metric_hit = any(bool(s.get("metric_hit")) for s in plan)
    fewshot_hit = any(bool(s.get("fewshot_hit")) for s in plan)
    verdict = final.get("verdict") or {}
    # reflect 只在真跑过（有 enough 键）时才算数
    reflect_enough = verdict.get("enough") if "enough" in verdict else None
    signals = collect_signals_from_plan(
        plan,
        metric_hit=metric_hit,
        fewshot_hit=fewshot_hit,
        reflect_enough=reflect_enough,
    )
    return assess(signals)


def save_result(result: AgentResult, agent_ctx: AgentContext, goal: str) -> None:
    """把一次运行落库（`agent_runs` + `agent_steps`）。失败不影响对外回答。"""
    from app.models import AgentRun, AgentStep

    try:
        with agent_ctx.session_factory() as session:
            run = AgentRun(
                run_id=result.run_id,
                user_id=getattr(agent_ctx.user, "id", None),
                goal=goal,
                trigger="chat",
                status=result.status,
                answer=result.answer or None,
                confidence=result.confidence or None,
                confidence_reasons=result.confidence_reasons or None,
                step_count=len(result.steps),
                tool_calls=result.tool_calls,
                elapsed_ms=result.elapsed_ms,
                error=result.error or None,
            )
            session.add(run)
            session.flush()
            for seq, step in enumerate(result.steps, start=1):
                session.add(
                    AgentStep(
                        run_id=run.id,
                        seq=seq,
                        node=step.get("node", ""),
                        tool=step.get("tool"),
                        status=step.get("status", "ok"),
                        step_input=step.get("step_input"),
                        output=step.get("output"),
                        elapsed_ms=step.get("elapsed_ms", 0),
                    )
                )
            session.commit()
    except Exception:  # noqa: BLE001 —— 落库失败不影响回答
        pass
