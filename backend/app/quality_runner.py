"""巡检执行与落库：跑一条规则，把结果写进 data_quality_results。

与 `monitor/runner.py` 的分工：那是"指标值异常后调 Agent 归因"，
这里是"巡检并落库"。命中异常时**不自动调 Agent** —— 巡检要的是便宜且
每天都能跑，归因的钱花在 `monitor` 那边。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import (
    RESULT_ANOMALY,
    RESULT_FAILED,
    DataQualityResult,
    DataQualityRule,
    User,
)
from app.quality import QualityCheckError, check_rule

# 巡检异常也记审计（合规审计最常查"什么时候开始不对的"）
AUDIT_ACTION_ANOMALY = "quality.anomaly"


def run_rule(rule_id: int, *, session_factory, pg_engine) -> DataQualityResult:
    """跑一条巡检规则并落库。返回本次结果。

    任何失败都落成一条 `failed` 结果（不抛出）—— 调度线程不能被一条
    坏规则炸掉，而且"这条规则一直失败"本身就是要在看板上看到的信息。
    """
    with session_factory() as session:
        rule = session.get(DataQualityRule, rule_id)
        if rule is None:
            raise QualityCheckError(f"巡检规则不存在: {rule_id}")
        owner = session.get(User, rule.owner_user_id)
        if owner is None:
            raise QualityCheckError(
                f"巡检规则 {rule.name} 的 owner 用户不存在: {rule.owner_user_id}"
            )

        outcome = check_rule(rule, session=session, pg_engine=pg_engine, owner=owner)

        # checked_at 显式取值：列有 server_default，但那是插入时才生效，
        # 此刻对象上还是 None —— 拿它回填 last_run_at 会写进 NULL。
        checked_at = datetime.now(timezone.utc)
        result = DataQualityResult(
            rule_id=rule.id,
            status=outcome.status,
            value=outcome.value,
            baseline=outcome.baseline,
            change_pct=outcome.change_pct,
            message=outcome.message,
            error=outcome.error,
            elapsed_ms=outcome.elapsed_ms,
            checked_at=checked_at,
        )
        session.add(result)

        rule.last_run_at = checked_at
        rule.last_status = outcome.status

        if outcome.status == RESULT_ANOMALY:
            _record_audit(session, rule, result)

        session.commit()
        session.refresh(result)
        return result


def _record_audit(session, rule: DataQualityRule, result: DataQualityResult) -> None:
    """异常记一条审计。失败不影响巡检结果本身。"""
    from app.models import AuditLog

    try:
        session.add(
            AuditLog(
                user_id=rule.owner_user_id,
                channel="web",
                dataset=None,
                action=AUDIT_ACTION_ANOMALY,
                row_count=0,
                duration_ms=result.elapsed_ms,
                detail={
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "rule_type": rule.rule_type,
                    "value": result.value,
                    "baseline": result.baseline,
                    "change_pct": result.change_pct,
                    "message": result.message,
                    "checked_at": (
                        result.checked_at or datetime.now(timezone.utc)
                    ).isoformat(),
                },
            )
        )
    except Exception:  # noqa: BLE001 —— 审计失败不能让巡检失败
        pass


__all__ = ["run_rule", "AUDIT_ACTION_ANOMALY", "RESULT_ANOMALY", "RESULT_FAILED"]
