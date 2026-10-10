"""监控任务执行器：取指标 → 判异常 → 异常才调 Agent 归因 → 存简报 →（可选）推送。

成本模型：轮询是纯 SQL（便宜、可高频），只有命中异常才启动 Agent（贵）。
这样「每天无人值守跑」在经济上才站得住。

推送默认关闭：`notify_webhook_url` 为空时只把简报落成 `agent_artifacts` 草稿，
人看过再决定发不发；填了 webhook 才真发（等于显式授权）。
"""
import logging
from datetime import datetime, timezone

from app.assistant import tools
from app.assistant.agent import AgentContext, run_agent
from app.assistant.tools import run_scoped_sql
from app.monitor.detect import evaluate
from app.sync.alert import send_alert

logger = logging.getLogger(__name__)

DEFAULT_GOAL = "分析指标「{name}」异常的原因：当前值 {value}，{reason}。请下钻定位到具体维度，并给出可执行的建议。"


def _fetch_metric(pg_engine, metric_sql: str, tables: list) -> float:
    columns, rows = run_scoped_sql(pg_engine, metric_sql, tables, 1)
    if not rows:
        raise ValueError("指标 SQL 没有返回任何行")
    try:
        return float(rows[0][0])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"指标 SQL 返回的不是数值：{rows[0][0]!r}") from exc


def _finish(session, task, status: str, value, detail: dict) -> dict:
    task.last_run_at = datetime.now(timezone.utc)
    task.last_status = status
    task.last_value = value
    task.last_detail = detail
    session.commit()
    return {"task_id": task.id, "name": task.name, "status": status, "value": value, "detail": detail}


def _investigate_goal(config: dict, value: float, verdict) -> str:
    template = (config.get("investigate_goal") or "").strip() or DEFAULT_GOAL
    try:
        return template.format(name=config["name"], value=value, reason=verdict.reason)
    except (KeyError, IndexError):
        # 用户模板写错占位符时不要炸，退回默认目标
        return DEFAULT_GOAL.format(name=config["name"], value=value, reason=verdict.reason)


def run_monitor(task_id: int, session_factory, pg_engine, settings=None, agent_runner=None) -> dict:
    """跑一个监控任务。`agent_runner` 可注入，便于测试不真的调模型。"""
    from app.models import MonitorObservation, MonitorTask, User

    agent_runner = agent_runner or run_agent
    with session_factory() as session:
        task = session.get(MonitorTask, task_id)
        if task is None or not task.enabled:
            return {"task_id": task_id, "status": "skipped", "reason": "任务不存在或已停用"}
        owner = session.get(User, task.owner_user_id)
        if owner is None:
            return _finish(session, task, "failed", None, {"error": "owner 用户不存在"})

        try:
            schema = tools.load_schema(session_factory, pg_engine, owner)
            value = _fetch_metric(pg_engine, task.metric_sql, schema["tables"])
        except Exception as exc:  # noqa: BLE001 —— 取数失败不该中断整轮调度
            logger.warning("监控任务 %s 取数失败：%s", task_id, exc)
            return _finish(session, task, "failed", None, {"error": str(exc)[:300]})

        history = [
            row.value
            for row in session.query(MonitorObservation)
            .filter_by(task_id=task.id)
            .order_by(MonitorObservation.observed_at.desc(), MonitorObservation.id.desc())
            .limit(task.window)
            .all()
        ][::-1]  # 还原成时间升序

        verdict = evaluate(value, history, task.rule, task.threshold, task.direction)
        session.add(MonitorObservation(task_id=task.id, value=value))
        summary = _finish(
            session,
            task,
            "anomaly" if verdict.is_anomaly else "ok",
            value,
            {
                "reason": verdict.reason,
                "score": verdict.score,
                "baseline": verdict.baseline,
                "samples": len(history),
            },
        )
        config = {
            "name": task.name,
            "investigate_goal": task.investigate_goal,
            "notify_webhook_url": task.notify_webhook_url or "",
            "window": task.window,
        }

    if not verdict.is_anomaly:
        return summary

    # —— 只有命中异常才花 Agent 的钱 ——
    goal = _investigate_goal(config, value, verdict)
    agent_ctx = AgentContext(
        session_factory=session_factory, pg_engine=pg_engine, user=owner
    )
    result = agent_runner(goal, agent_ctx, None, True)
    brief = _build_brief(config, value, summary["detail"], result)
    artifact_id = _save_brief(result.run_id, config["name"], brief, value, agent_ctx)
    notified = False
    if config["notify_webhook_url"]:
        notified = send_alert(
            config["notify_webhook_url"], f"【监控告警】{config['name']}", brief
        )
    return {
        **summary,
        "investigation": {
            "goal": goal,
            "run_id": result.run_id,
            "status": result.status,
            "answer": result.answer,
            "artifact_id": artifact_id,
            "notified": notified,
        },
    }


def _build_brief(config: dict, value: float, detail: dict, result) -> str:
    lines = [
        f"指标：{config['name']}",
        f"当前值：{value}",
        f"判定：{detail.get('reason', '')}",
        f"历史样本：{detail.get('samples', 0)} 个（窗口 {config.get('window')}）",
        "",
        "归因结论：",
        result.answer or "（Agent 未给出结论）",
    ]
    if result.plan:
        lines.append("")
        lines.append("归因路径：")
        for step in result.plan:
            mark = {"done": "✓", "failed": "✗"}.get(step.get("status"), "·")
            lines.append(f"  {mark} {step.get('question')}")
    return "\n".join(lines)


def _save_brief(run_id: str, name: str, brief: str, value: float, agent_ctx) -> int | None:
    """把简报落成 artifact 草稿，供人查看/确认后再发。"""
    from app.models import AgentArtifact, AgentRun

    try:
        with agent_ctx.session_factory() as session:
            run = session.query(AgentRun).filter_by(run_id=run_id).first()
            if run is None:
                return None
            artifact = AgentArtifact(
                run_id=run.id,
                kind="alert",
                title=f"【监控告警】{name}",
                content=brief,
                meta={"value": value},
            )
            session.add(artifact)
            session.commit()
            return artifact.id
    except Exception:  # noqa: BLE001 —— 存草稿失败不影响已完成的归因
        logger.exception("监控简报落库失败")
        return None
