"""Agent SSE 接口测试：直接驱动 `_run` 生成器与纯校验函数，不经过 HTTP 层。"""
import json

from app.assistant import agent_api


class FakeSettings:
    agent_enabled = True
    assistant_max_rows = 100
    agent_evidence_rows = 20
    agent_max_sub_questions = 3
    agent_max_replans = 1
    agent_max_tool_calls = 5
    agent_max_sql_attempts = 2
    agent_clarify_enabled = True


def _ctx(enabled=True):
    settings = FakeSettings()
    settings.agent_enabled = enabled
    return type(
        "Ctx", (), {"settings": settings, "session_factory": lambda: None, "pg_engine": object()}
    )()


def _principal():
    return type("P", (), {"user": type("U", (), {"id": None})()})()


def _events(gen):
    out = []
    for chunk in gen:
        text = chunk.strip()
        if text.startswith("data: "):
            out.append(json.loads(text[6:]))
    return out


def test_check_request_rejects_when_disabled():
    settings = FakeSettings()
    settings.agent_enabled = False
    assert "未启用" in agent_api.check_request("目标", settings)


def test_check_request_rejects_empty_goal():
    assert "不能为空" in agent_api.check_request("", FakeSettings())


def test_check_request_passes():
    assert agent_api.check_request("目标", FakeSettings()) is None


def test_run_emits_run_plan_step_answer(monkeypatch):
    def fake_stream(goal, agent_ctx):
        yield "plan", {"plan": [{"id": 1, "question": "子问题A", "status": "pending"}]}
        yield "act", {"steps": [{"node": "act", "tool": "run_sql", "status": "ok", "elapsed_ms": 5}]}
        yield "reflect", {"steps": [{"node": "reflect", "tool": None, "status": "ok", "elapsed_ms": 1}]}
        yield "__final__", {
            "answer": "华东毛利下降主要因为退货率上升。",
            "plan": [{"question": "子问题A", "status": "done", "sql": "SELECT 1", "row_count": 2}],
            "steps": [{"node": "plan"}, {"node": "act"}, {"node": "answer"}],
            "tool_calls": 1,
        }

    monkeypatch.setattr(agent_api, "stream_agent", fake_stream)
    monkeypatch.setattr(agent_api, "save_result", lambda *a, **k: None)

    events = _events(agent_api._run("目标", _principal(), _ctx()))
    # 每个节点都会发一条 step；plan 节点额外先发一条 plan
    assert [e["type"] for e in events] == ["run", "plan", "step", "step", "step", "answer", "done"]
    assert events[1]["sub_questions"] == ["子问题A"]
    assert [e["node"] for e in events[2:5]] == ["plan", "act", "reflect"]
    assert events[5]["answer"].startswith("华东毛利")
    assert events[5]["status"] == "ok"
    assert events[5]["plan"][0]["row_count"] == 2


def test_run_reports_stream_failure(monkeypatch):
    def boom(goal, agent_ctx):
        yield "plan", {"plan": [{"id": 1, "question": "A", "status": "pending"}]}
        raise RuntimeError("取了半天没取到")

    monkeypatch.setattr(agent_api, "stream_agent", boom)
    monkeypatch.setattr(agent_api, "save_result", lambda *a, **k: None)

    events = _events(agent_api._run("目标", _principal(), _ctx()))
    assert [e["type"] for e in events] == ["run", "plan", "step", "error", "answer", "done"]
    assert "取了半天没取到" in events[3]["message"]
    assert events[4]["status"] == "failed"


def test_run_persists_with_same_run_id(monkeypatch):
    captured = {}

    def fake_stream(goal, agent_ctx):
        yield "__final__", {"answer": "结论", "plan": [], "steps": [], "tool_calls": 0}

    monkeypatch.setattr(agent_api, "stream_agent", fake_stream)
    monkeypatch.setattr(agent_api, "save_result", lambda result, ctx, goal: captured.update(
        {"run_id": result.run_id, "goal": goal, "status": result.status}
    ))

    events = _events(agent_api._run("目标", _principal(), _ctx()))
    assert captured["goal"] == "目标"
    assert captured["status"] == "ok"
    # 对外发出的 run_id 与落库的一致
    assert events[0]["run_id"] == captured["run_id"]
