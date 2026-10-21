"""数据质量巡检的接口：规则 CRUD、结果查询、手动触发。

## 谁能看巡检结果

**登录即可看**（与血缘同一个取舍）：巡检结果的价值在于协作 ——
业务人员要能自己确认"这批数今天到底有没有问题"，按权限裁剪会让
看不到的人以为没事，那比看不到更危险。

泄露面控制住：只有 value/baseline/message，**没有行数据**。

## 谁能建/改规则

仅管理员。规则决定"什么算异常"，是治理动作。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.models import (
    RESULT_FAILED,
    RESULT_SKIPPED,
    RULE_TYPES,
    DataQualityResult,
    DataQualityRule,
    Dataset,
    User,
)

router = APIRouter(prefix="/quality", tags=["quality"])


def _require_admin(principal: AnyPrincipal) -> None:
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")


def _tenant_id(principal: AnyPrincipal) -> int | None:
    return getattr(principal.tenant, "tenant_id", None)


def _rule_dict(session, rule: DataQualityRule) -> dict:
    ds = session.get(Dataset, rule.dataset_id)
    owner = session.get(User, rule.owner_user_id)
    return {
        "id": rule.id,
        "name": rule.name,
        "dataset_id": rule.dataset_id,
        "dataset_name": ds.name if ds else None,
        "enabled": rule.enabled,
        "rule_type": rule.rule_type,
        "threshold": rule.threshold,
        "target_field": rule.target_field,
        "timestamp_field": rule.timestamp_field,
        "direction": rule.direction,
        "cron": rule.cron,
        "owner_user_id": rule.owner_user_id,
        "owner_name": owner.username if owner else None,
        "last_run_at": rule.last_run_at.isoformat() if rule.last_run_at else None,
        "last_status": rule.last_status,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
    }


@router.get("/rules")
def list_rules(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """列出租户内的巡检规则。"""
    tenant_id = _tenant_id(principal)
    with ctx.session_factory() as session:
        q = session.query(DataQualityRule)
        if tenant_id is not None:
            q = q.filter(DataQualityRule.tenant_id == tenant_id)
        return [_rule_dict(session, r) for r in q.order_by(DataQualityRule.id).all()]


@router.post("/rules", status_code=201)
def create_rule(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """建一条巡检规则（管理员）。"""
    _require_admin(principal)
    name = (body.get("name") or "").strip()
    rule_type = body.get("rule_type")
    dataset_id = body.get("dataset_id")
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if rule_type not in RULE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"rule_type 必须是 {', '.join(RULE_TYPES)} 之一",
        )
    if not dataset_id:
        raise HTTPException(status_code=400, detail="dataset_id required")

    from app.access.scoped_query import check_ident

    for field_name in ("target_field", "timestamp_field"):
        value = body.get(field_name)
        if value:
            try:
                check_ident(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

    with ctx.session_factory() as session:
        if session.query(DataQualityRule).filter_by(name=name).first():
            raise HTTPException(status_code=409, detail="rule name exists")
        ds = session.get(Dataset, dataset_id)
        if ds is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        tenant_id = _tenant_id(principal)
        if tenant_id is not None and ds.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="dataset not found")

        if rule_type == "null_rate" and not body.get("target_field"):
            raise HTTPException(
                status_code=400, detail="null_rate 规则必须指定 target_field"
            )

        try:
            threshold = float(body.get("threshold", 20.0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="threshold 必须是数字")
        if threshold <= 0:
            raise HTTPException(status_code=400, detail="threshold 必须大于 0")

        rule = DataQualityRule(
            tenant_id=tenant_id if tenant_id is not None else ds.tenant_id,
            name=name,
            dataset_id=ds.id,
            rule_type=rule_type,
            threshold=threshold,
            target_field=body.get("target_field"),
            timestamp_field=body.get("timestamp_field"),
            direction=body.get("direction", "both"),
            cron=body.get("cron") or "0 7 * * *",
            owner_user_id=body.get("owner_user_id") or principal.user.id,
        )
        session.add(rule)
        session.commit()
        return {"id": rule.id, "name": rule.name}


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """改规则（管理员）。改完需要重挂调度任务。"""
    _require_admin(principal)
    with ctx.session_factory() as session:
        rule = session.get(DataQualityRule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        tenant_id = _tenant_id(principal)
        if tenant_id is not None and rule.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="rule not found")

        for field_name in ("threshold", "target_field", "timestamp_field",
                           "direction", "cron", "enabled"):
            if field_name in body:
                setattr(rule, field_name, body[field_name])
        if "threshold" in body:
            try:
                rule.threshold = float(body["threshold"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="threshold 必须是数字")
        session.commit()
        return _rule_dict(session, rule)


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """删规则（连带删它的历史结果 —— 结果是规则的附庸，没有独立价值）。"""
    _require_admin(principal)
    with ctx.session_factory() as session:
        rule = session.get(DataQualityRule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        tenant_id = _tenant_id(principal)
        if tenant_id is not None and rule.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="rule not found")
        session.query(DataQualityResult).filter_by(rule_id=rule.id).delete(
            synchronize_session=False
        )
        session.delete(rule)
        session.commit()
        return {"deleted": rule_id}


@router.get("/results")
def list_results(
    rule_id: int = Query(None),
    status: str = Query(""),
    limit: int = Query(50, le=200),
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """查巡检结果（登录即可，与血缘同一个取舍）。"""
    tenant_id = _tenant_id(principal)
    with ctx.session_factory() as session:
        q = session.query(DataQualityResult).join(
            DataQualityRule, DataQualityResult.rule_id == DataQualityRule.id
        )
        if tenant_id is not None:
            q = q.filter(DataQualityRule.tenant_id == tenant_id)
        if rule_id:
            q = q.filter(DataQualityResult.rule_id == rule_id)
        if status:
            q = q.filter(DataQualityResult.status == status)
        rows = q.order_by(DataQualityResult.id.desc()).limit(limit).all()
        return [_result_dict(session, r) for r in rows]


def _result_dict(session, result: DataQualityResult) -> dict:
    rule = session.get(DataQualityRule, result.rule_id)
    ds = session.get(Dataset, rule.dataset_id) if rule else None
    return {
        "id": result.id,
        "rule_id": result.rule_id,
        "rule_name": rule.name if rule else None,
        "rule_type": rule.rule_type if rule else None,
        "dataset_name": ds.name if ds else None,
        "status": result.status,
        "value": result.value,
        "baseline": result.baseline,
        "change_pct": result.change_pct,
        "message": result.message,
        "error": result.error,
        "elapsed_ms": result.elapsed_ms,
        "checked_at": result.checked_at.isoformat() if result.checked_at else None,
    }


@router.post("/rules/{rule_id}/run")
def run_now(
    rule_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """手动跑一次巡检（同步等结果，便于"我现在就要知道"）。"""
    with ctx.session_factory() as session:
        rule = session.get(DataQualityRule, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        tenant_id = _tenant_id(principal)
        if tenant_id is not None and rule.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="rule not found")

    from app.quality_runner import run_rule

    result = run_rule(rule_id, session_factory=ctx.session_factory, pg_engine=ctx.pg_engine)
    return _result_dict(session, result)


@router.get("/summary")
def summary(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """看板用的汇总：几条规则、最近一次各自状态、最近异常。"""
    tenant_id = _tenant_id(principal)
    with ctx.session_factory() as session:
        q = session.query(DataQualityRule)
        if tenant_id is not None:
            q = q.filter(DataQualityRule.tenant_id == tenant_id)
        rules = q.all()
        items = []
        for rule in rules:
            latest = (
                session.query(DataQualityResult)
                .filter_by(rule_id=rule.id)
                .order_by(DataQualityResult.id.desc())
                .first()
            )
            items.append(
                {
                    "rule_id": rule.id,
                    "name": rule.name,
                    "rule_type": rule.rule_type,
                    "enabled": rule.enabled,
                    "last_status": rule.last_status,
                    "last_run_at": (
                        rule.last_run_at.isoformat() if rule.last_run_at else None
                    ),
                    "latest_message": latest.message if latest else "",
                }
            )
        anomaly_count = sum(
            1 for i in items if i["last_status"] == "anomaly"
        )
        return {
            "total": len(items),
            "anomaly": anomaly_count,
            "failed": sum(1 for i in items if i["last_status"] == RESULT_FAILED),
            "rules": items,
        }
