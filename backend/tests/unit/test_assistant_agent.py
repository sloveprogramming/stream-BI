"""Agent 循环测试：注入假 LLM 与假取数，不碰网络与真实数据库。"""
from dataclasses import dataclass

import pytest

from app.assistant import agent as agent_mod
from app.assistant.agent import (
    ANSWER_SYSTEM,
    CLARIFY_SYSTEM,
    PLAN_SYSTEM,
    REFLECT_SYSTEM,
    AgentContext,
    run_agent,
)
from app.assistant.tools import SqlGuardError
from app.metrics import MetricDictionary

SCHEMA = {
    "tables": [{"name": "sales_order", "source_table": "ods.sales_order", "columns": ["id"]}],
    "prompt": "- 表 sales_order 字段: id",
}


class FakeLlm:
    """按 system prompt 区分「规划 / 反思 / 出 SQL / 作答」四种调用。"""

    def __init__(self, subs=None, verdict=None, answer="华东毛利下降主要因为退货率上升。",
                 sql_text="SELECT 1", clarify=None):
        self.subs = subs if subs is not None else ["查华东毛利趋势", "查各渠道退货率"]
        self.verdict = verdict if verdict is not None else {"enough": True, "reason": "证据充分"}
        self.answer = answer
        # 注意：不能存成 self.sql，否则会覆盖下面的 sql() 方法
        self.sql_text = sql_text
        self.clarify = clarify if clarify is not None else {"ambiguous": False}
        self.plan_calls = 0
        self.sql_calls = 0
        self.clarify_calls = 0

    def sql(self, system, user):
        self.sql_calls += 1
        assert system  # 复用生产提示词
        return self.sql_text

    def json(self, system, user):
        if system == CLARIFY_SYSTEM:
            self.clarify_calls += 1
            return self.clarify
        if system == PLAN_SYSTEM:
            self.plan_calls += 1
            return {"sub_questions": self.subs}
        assert system == REFLECT_SYSTEM
        return self.verdict

    def text(self, system, user):
        assert system == ANSWER_SYSTEM
        return self.answer


@pytest.fixture
def patched(monkeypatch):
    """把 schema 加载与取数都换成假的。"""
    monkeypatch.setattr(agent_mod.tools, "load_schema", lambda *a, **k: SCHEMA)
    calls = []

    def fake_run_scoped_sql(pg_engine, sql, tables, max_rows, timeout_seconds=None):
        calls.append({"sql": sql, "tables": tables})
        return ["id", "amount"], [[1, 100], [2, 200]]

    monkeypatch.setattr(agent_mod, "run_scoped_sql", fake_run_scoped_sql)
    return calls


def _ctx(**kw):
    """测试默认接一个**空**指标字典：这些用例聚焦 Agent 循环本身，
    不该顺带依赖仓库里的 `metrics.yml`。
    口径歧义那条路径由 `test_assistant_metrics.py` 单独覆盖。
    """
    params = dict(
        session_factory=lambda: None,
        pg_engine=object(),
        user=None,
        max_rows=100,
        metrics_dictionary=MetricDictionary(),
    )
    params.update(kw)
    return AgentContext(**params)


def test_agent_plans_and_runs_all_sub_questions(patched):
    llm = FakeLlm(subs=["子问题A", "子问题B", "子问题C"])
    result = run_agent("华东毛利为什么下降", _ctx(), llm=llm, persist=False)

    assert result.status == "ok"
    assert result.tool_calls == 3
    assert llm.sql_calls == 3
    assert [s["status"] for s in result.plan] == ["done", "done", "done"]
    assert result.answer.startswith("华东毛利下降")
    assert [st["node"] for st in result.steps].count("plan") == 1


def test_agent_traces_include_every_node(patched):
    result = run_agent("目标", _ctx(), llm=FakeLlm(subs=["一"]), persist=False)
    nodes = [st["node"] for st in result.steps]
    assert nodes[0] == "clarify"
    assert nodes[-1] == "answer"
    assert {"plan", "act", "reflect", "answer"} <= set(nodes)


def test_agent_falls_back_when_plan_is_unusable(patched, monkeypatch):
    """规划返回垃圾时，退化用原问题当唯一子问题，而不是直接失败。"""
    llm = FakeLlm(subs=[])
    result = run_agent("原问题", _ctx(), llm=llm, persist=False)
    assert len(result.plan) == 1
    assert result.plan[0]["question"] == "原问题"


def test_agent_caps_sub_questions(patched):
    llm = FakeLlm(subs=[f"子问题{i}" for i in range(20)])
    result = run_agent("目标", _ctx(max_sub_questions=3), llm=llm, persist=False)
    assert len(result.plan) == 3


def test_agent_survives_guard_rejection(patched, monkeypatch):
    """单步被安全闸门拦下时，不中断整轮，仍给出结论并记录失败步骤。"""

    def boom(*a, **k):
        raise SqlGuardError("SQL 越过字段白名单")

    monkeypatch.setattr(agent_mod, "run_scoped_sql", boom)
    result = run_agent("目标", _ctx(), llm=FakeLlm(subs=["一", "二"]), persist=False)

    assert result.status == "ok"
    assert [s["status"] for s in result.plan] == ["failed", "failed"]
    assert "越过字段白名单" in result.plan[0]["error"]
    assert result.answer


def test_agent_replans_once_when_evidence_insufficient(patched):
    """证据不足 → 重规划一次（上限 max_replans），然后必须收敛到 answer。"""
    llm = FakeLlm(subs=["一"], verdict={"enough": False, "reason": "还是不够"})
    result = run_agent("目标", _ctx(max_replans=1), llm=llm, persist=False)

    assert llm.plan_calls == 2  # 初次规划 + 一次重规划
    assert result.answer
    assert any(st["node"] == "reflect" for st in result.steps)


def test_agent_stops_at_max_tool_calls(patched):
    llm = FakeLlm(subs=["一", "二", "三", "四"])
    result = run_agent("目标", _ctx(max_tool_calls=2), llm=llm, persist=False)
    assert result.tool_calls <= 2
    assert result.answer


def test_agent_reports_fatal_error(patched, monkeypatch):
    """图整体抛错时状态应为 failed，而不是假装成功。"""

    class ExplodingGraph:
        def stream(self, *a, **k):
            raise RuntimeError("图炸了")

    monkeypatch.setattr(agent_mod, "build_graph", lambda ctx, llm=None: ExplodingGraph())
    result = run_agent("目标", _ctx(), llm=FakeLlm(), persist=False)
    assert result.status == "failed"
    assert "图炸了" in result.error


def test_stream_agent_yields_nodes_then_final(patched):
    from app.assistant.agent import stream_agent

    events = list(stream_agent("目标", _ctx(), llm=FakeLlm(subs=["一"])))
    nodes = [name for name, _ in events]
    assert nodes[0] == "clarify"
    assert nodes[-1] == "__final__"
    assert set(nodes) >= {"clarify", "plan", "act", "reflect", "answer"}
    final = events[-1][1]
    assert final["answer"]
    assert len(final["steps"]) >= 5


# ---------------------------------------------------------------- Step 4：反思重写 / 澄清


def test_agent_retries_broken_sql_then_succeeds(patched, monkeypatch):
    """第一次执行报错 → 把报错喂回模型改写 → 第二次成功。"""
    attempts = {"n": 0}

    def flaky(pg_engine, sql, tables, max_rows, timeout_seconds=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError('column "amount2" does not exist')
        return ["id"], [[1]]

    monkeypatch.setattr(agent_mod, "run_scoped_sql", flaky)
    llm = FakeLlm(subs=["一"])
    result = run_agent("目标", _ctx(max_sql_attempts=2), llm=llm, persist=False)

    assert result.status == "ok"
    assert result.plan[0]["status"] == "done"
    assert llm.sql_calls == 2       # 生成两次：原始 + 按报错改写
    assert result.tool_calls == 2


def test_agent_marks_step_failed_after_retries_exhausted(patched, monkeypatch):
    def always_broken(pg_engine, sql, tables, max_rows, timeout_seconds=None):
        raise RuntimeError("表不存在")

    monkeypatch.setattr(agent_mod, "run_scoped_sql", always_broken)
    llm = FakeLlm(subs=["一"])
    result = run_agent("目标", _ctx(max_sql_attempts=2), llm=llm, persist=False)

    assert result.plan[0]["status"] == "failed"
    assert "表不存在" in result.plan[0]["error"]
    assert llm.sql_calls == 2       # 重试有界，不会无限打转
    assert result.answer            # 仍然给出结论（说明证据不足）


def test_agent_asks_for_clarification_instead_of_guessing(patched):
    llm = FakeLlm(
        clarify={
            "ambiguous": True,
            "question": "销售额按支付口径还是回款口径？",
            "options": ["支付口径", "回款口径"],
        }
    )
    result = run_agent("销售额是多少", _ctx(), llm=llm, persist=False)

    assert result.status == "needs_clarification"
    assert "口径" in result.answer
    assert result.clarification["options"] == ["支付口径", "回款口径"]
    # 关键：没有瞎猜去查
    assert llm.sql_calls == 0
    assert llm.plan_calls == 0


def test_agent_skips_clarification_when_disabled(patched):
    llm = FakeLlm(subs=["一"])
    result = run_agent("目标", _ctx(clarify_enabled=False), llm=llm, persist=False)
    assert llm.clarify_calls == 0
    assert result.status == "ok"


def test_run_agent_persists_run_and_steps(pg_session_factory, pg_engine, patched):
    from app.models import AgentRun, AgentStep

    result = run_agent(
        "华东毛利为什么下降",
        _ctx(session_factory=pg_session_factory, pg_engine=pg_engine),
        llm=FakeLlm(subs=["一", "二"]),
        persist=True,
    )
    with pg_session_factory() as s:
        run = s.query(AgentRun).filter_by(run_id=result.run_id).first()
        assert run is not None
        assert run.status == "ok"
        assert run.tool_calls == 2
        steps = s.query(AgentStep).filter_by(run_id=run.id).order_by(AgentStep.seq).all()
        assert [st.seq for st in steps] == list(range(1, len(steps) + 1))
        assert steps[0].node == "clarify"
        # 清理
        s.query(AgentStep).filter_by(run_id=run.id).delete()
        s.query(AgentRun).filter_by(id=run.id).delete()
        s.commit()


def test_run_agent_can_skip_persistence(pg_session_factory, pg_engine, patched):
    from app.models import AgentRun

    result = run_agent("目标", _ctx(session_factory=pg_session_factory, pg_engine=pg_engine),
                       llm=FakeLlm(subs=["一"]), persist=False)
    with pg_session_factory() as s:
        assert s.query(AgentRun).filter_by(run_id=result.run_id).first() is None
