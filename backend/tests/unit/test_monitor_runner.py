"""监控任务执行器测试：真库 + 注入假取数与假 Agent，不碰网络与模型。"""
import uuid

import pytest

from app.assistant.agent import AgentResult
from app.monitor import runner as runner_mod
from app.monitor.runner import run_monitor


@pytest.fixture
def mon_env(pg_session_factory, pg_engine, monkeypatch):
    """造一个监控任务；取数被替换成返回预设值。"""
    from app.models import MonitorObservation, MonitorTask, User

    suffix = uuid.uuid4().hex[:8]
    with pg_session_factory() as s:
        owner = User(username=f"mon_u_{suffix}", password_hash="x", is_admin=True)
        s.add(owner)
        s.flush()
        task = MonitorTask(
            name=f"mon_{suffix}",
            owner_user_id=owner.id,
            metric_sql="SELECT 1",
            investigate_goal="分析指标「{name}」：当前 {value}，{reason}",
            rule="pct_change",
            threshold=10.0,
            direction="down",
            window=5,
            cron="0 8 * * *",
            notify_webhook_url="",
        )
        s.add(task)
        s.commit()
        ids = {"task": task.id, "owner": owner.id, "name": task.name}
    # 取数不需要真表：schema 与执行都替换掉
    monkeypatch.setattr(runner_mod.tools, "load_schema", lambda *a, **k: {"tables": [], "prompt": ""})
    monkeypatch.setattr(runner_mod, "run_scoped_sql", lambda *a, **k: (["v"], [["100"]]))

    yield ids, pg_session_factory, pg_engine

    with pg_session_factory() as s:
        s.query(MonitorObservation).filter_by(task_id=ids["task"]).delete()
        from app.models import AgentArtifact, AgentRun, MonitorTask as MT

        for run in s.query(AgentRun).filter(AgentRun.goal.like("%mon_%")).all():
            s.query(AgentArtifact).filter_by(run_id=run.id).delete()
            s.delete(run)
        s.query(MT).filter_by(id=ids["task"]).delete()
        s.query(User).filter_by(id=ids["owner"]).delete()
        s.commit()


def _fake_agent(recorder):
    def _run(goal, ctx, llm=None, persist=True):
        from app.models import AgentRun

        run_id = f"run_monitor_{uuid.uuid4().hex[:12]}"
        recorder.append(goal)
        with ctx.session_factory() as s:
            s.add(
                AgentRun(
                    run_id=run_id, goal=goal, trigger="monitor", status="ok",
                    answer="主因是某仓退货率上升。", step_count=2, tool_calls=2,
                )
            )
            s.commit()
        return AgentResult(
            run_id=run_id, status="ok", answer="主因是某仓退货率上升。",
            plan=[{"question": "查退货率", "status": "done"}], tool_calls=2,
        )

    return _run


def test_skips_disabled_or_missing_task(mon_env, pg_session_factory, pg_engine):
    ids, sf, engine = mon_env
    assert run_monitor(999999, sf, engine)["status"] == "skipped"


def test_normal_result_does_not_invoke_agent(mon_env, monkeypatch):
    ids, sf, engine = mon_env
    calls = []
    monkeypatch.setattr(runner_mod, "run_scoped_sql", lambda *a, **k: (["v"], [["100"]]))
    result = run_monitor(ids["task"], sf, engine, agent_runner=_fake_agent(calls))
    # 没有历史基线 → 判为正常，且**没有**启动 Agent（省钱）
    assert result["status"] == "ok"
    assert calls == []
    assert result["value"] == 100.0


def test_anomaly_triggers_agent_and_saves_brief(mon_env, monkeypatch, pg_session_factory):
    from app.models import AgentArtifact, MonitorObservation

    ids, sf, engine = mon_env
    # 先塞入历史值 100，再把当前值降到 50 → 环比 -50% 命中阈值
    with sf() as s:
        s.add(MonitorObservation(task_id=ids["task"], value=100.0))
        s.commit()
    monkeypatch.setattr(runner_mod, "run_scoped_sql", lambda *a, **k: (["v"], [["50"]]))

    calls = []
    result = run_monitor(ids["task"], sf, engine, agent_runner=_fake_agent(calls))

    assert result["status"] == "anomaly"
    assert calls and "mon_" in calls[0]        # Agent 被唤起，且目标是模板渲染后的
    assert result["investigation"]["answer"] == "主因是某仓退货率上升。"
    assert result["investigation"]["notified"] is False   # 没配 webhook 就不发
    assert result["investigation"]["artifact_id"] is not None

    with sf() as s:
        artifact = s.query(AgentArtifact).filter_by(id=result["investigation"]["artifact_id"]).one()
        assert "主因是某仓退货率上升" in artifact.content
        assert "环比" in artifact.content
        assert artifact.kind == "alert"


def test_metric_failure_marks_task_failed(mon_env, monkeypatch):
    ids, sf, engine = mon_env

    def boom(*a, **k):
        raise ValueError("指标 SQL 返回的不是数值：'abc'")

    monkeypatch.setattr(runner_mod, "run_scoped_sql", boom)
    result = run_monitor(ids["task"], sf, engine)
    assert result["status"] == "failed"
    assert "不是数值" in result["detail"]["error"]


def test_empty_metric_result_marks_failed(mon_env, monkeypatch):
    ids, sf, engine = mon_env
    monkeypatch.setattr(runner_mod, "run_scoped_sql", lambda *a, **k: (["v"], []))
    result = run_monitor(ids["task"], sf, engine)
    assert result["status"] == "failed"
    assert "没有返回任何行" in result["detail"]["error"]


def test_observation_recorded_each_run(mon_env, monkeypatch):
    from app.models import MonitorObservation

    ids, sf, engine = mon_env
    monkeypatch.setattr(runner_mod, "run_scoped_sql", lambda *a, **k: (["v"], [["77"]]))
    run_monitor(ids["task"], sf, engine)
    run_monitor(ids["task"], sf, engine)
    with sf() as s:
        rows = s.query(MonitorObservation).filter_by(task_id=ids["task"]).all()
        assert [row.value for row in rows] == [77.0, 77.0]


def test_bad_goal_template_falls_back_to_default(mon_env, monkeypatch):
    """用户把占位符写错时不能炸，要退回默认归因目标。"""
    from app.models import MonitorObservation, MonitorTask

    ids, sf, engine = mon_env
    with sf() as s:
        task = s.get(MonitorTask, ids["task"])
        task.investigate_goal = "分析 {不存在的占位符}"
        s.add(MonitorObservation(task_id=task.id, value=100.0))
        s.commit()
    monkeypatch.setattr(runner_mod, "run_scoped_sql", lambda *a, **k: (["v"], [["10"]]))
    calls = []
    result = run_monitor(ids["task"], sf, engine, agent_runner=_fake_agent(calls))
    assert result["status"] == "anomaly"
    assert "异常的原因" in calls[0]
