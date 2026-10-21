"""权限申请与审批：让业务人员能申请、管理员审批，审批通过自动落成授权。

## 端点

| 方法 | 路径 | 谁能用 |
|---|---|---|
| POST | `/api/v1/grant-requests` | 任何登录用户（为自己申请） |
| GET | `/api/v1/grant-requests` | 登录用户（`mine=1` 看我提的；否则看待我审批的） |
| POST | `/api/v1/grant-requests/{id}/approve` | 管理员（只能审批自己租户内的） |
| POST | `/api/v1/grant-requests/{id}/reject` | 管理员 |
| POST | `/api/v1/grant-requests/{id}/cancel` | 申请人本人 |

## 三条安全约束

1. **不能审批自己提的申请**（`applicant == 审批人` 时拒绝）——
   否则权限自批，申请流失去意义；
2. **不能跨租户审批**：`tenant_id` 必须与审批人一致；
3. **审批时校验请求的行级规则/字段白名单**是否合法（字段必须在数据集里），
   与 `admin_api.create_grant` 同一套校验 —— 不因为走了申请流就放松。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.grants import expires_at_from_days
from app.models import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Dataset,
    DatasetGrant,
    GrantRequest,
)

router = APIRouter(prefix="/grant-requests", tags=["grants"])

_VALID_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "like"}


def _tenant_id(principal: AnyPrincipal) -> int | None:
    return getattr(principal.tenant, "tenant_id", None)


def _validate_requested(session, ds: Dataset, row_rules: list, whitelist) -> None:
    """校验申请里声明的期望权限是否合法（与管理员直建授权同一套规则）。"""
    if whitelist is not None:
        bad = set(whitelist) - set(ds.columns or [])
        if bad:
            raise HTTPException(
                status_code=400, detail=f"白名单字段不存在: {sorted(bad)}"
            )
    for rule in row_rules or []:
        if rule.get("field") not in (ds.columns or []):
            raise HTTPException(
                status_code=400, detail=f"规则字段不存在: {rule.get('field')}"
            )
        if rule.get("op") not in _VALID_OPS:
            raise HTTPException(
                status_code=400, detail=f"非法操作符: {rule.get('op')}"
            )


@router.post("", status_code=201)
def create_request(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """提一条权限申请。只能为自己申请（不提供 user_id 参数）。"""
    dataset_id = body.get("dataset_id")
    reason = (body.get("reason") or "").strip()
    if not dataset_id:
        raise HTTPException(status_code=400, detail="dataset_id required")
    if not reason:
        raise HTTPException(status_code=400, detail="reason required（申请理由必填）")
    if len(reason) < 5:
        raise HTTPException(status_code=400, detail="申请理由太短，请说明用途")

    with ctx.session_factory() as session:
        ds = session.get(Dataset, dataset_id)
        if ds is None or not ds.enabled:
            raise HTTPException(status_code=404, detail="dataset not found")
        tenant_id = _tenant_id(principal)
        if tenant_id is not None and ds.tenant_id != tenant_id:
            # 与 data_api 一致：不区分"不存在"与"别的租户"
            raise HTTPException(status_code=404, detail="dataset not found")

        row_rules = body.get("row_rules") or []
        whitelist = body.get("field_whitelist")
        _validate_requested(session, ds, row_rules, whitelist)

        days = body.get("days")
        if days is not None:
            try:
                days = int(days)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="days 必须是整数")
            if days <= 0:
                raise HTTPException(status_code=400, detail="days 必须大于 0")

        # 已生效的授权不用再申请
        from app.access.grants import resolve_grant

        if resolve_grant(session, principal.user, ds.id) is not None:
            raise HTTPException(status_code=409, detail="你已有该数据集的权限")

        # 同一数据集只允许一条 pending 申请（避免刷屏）
        pending = (
            session.query(GrantRequest)
            .filter_by(
                applicant_user_id=principal.user.id,
                dataset_id=ds.id,
                status=STATUS_PENDING,
            )
            .first()
        )
        if pending:
            raise HTTPException(
                status_code=409,
                detail="你已有一条待审批的申请，请等待处理",
            )

        req = GrantRequest(
            tenant_id=tenant_id if tenant_id is not None else ds.tenant_id,
            applicant_user_id=principal.user.id,
            dataset_id=ds.id,
            status=STATUS_PENDING,
            reason=reason,
            requested_row_rules=row_rules,
            requested_field_whitelist=whitelist,
            requested_days=days,
        )
        session.add(req)
        session.commit()
        return {"id": req.id, "status": req.status, "dataset": ds.name}


@router.get("")
def list_requests(
    mine: bool = Query(False),
    status: str = Query(""),
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """`mine=1` 看我提的申请；否则看我待审批的（管理员）。"""
    tenant_id = _tenant_id(principal)
    with ctx.session_factory() as session:
        q = session.query(GrantRequest)
        if tenant_id is not None:
            q = q.filter(GrantRequest.tenant_id == tenant_id)
        if mine:
            q = q.filter(GrantRequest.applicant_user_id == principal.user.id)
        else:
            if not principal.user.is_admin:
                raise HTTPException(status_code=403, detail="admin only")
            q = q.filter(GrantRequest.status == STATUS_PENDING)
        if status:
            q = q.filter(GrantRequest.status == status)
        rows = q.order_by(GrantRequest.id.desc()).limit(200).all()
        return [_to_dict(session, r) for r in rows]


def _to_dict(session, req: GrantRequest) -> dict:
    from app.models import User

    applicant = session.get(User, req.applicant_user_id)
    decided_by = (
        session.get(User, req.decided_by_user_id) if req.decided_by_user_id else None
    )
    return {
        "id": req.id,
        "status": req.status,
        "reason": req.reason,
        "decision_note": req.decision_note,
        "dataset_id": req.dataset_id,
        "applicant": applicant.username if applicant else None,
        "applicant_id": req.applicant_user_id,
        "decided_by": decided_by.username if decided_by else None,
        "requested_row_rules": req.requested_row_rules,
        "requested_field_whitelist": req.requested_field_whitelist,
        "requested_days": req.requested_days,
        "granted_id": req.granted_id,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "decided_at": req.decided_at.isoformat() if req.decided_at else None,
    }


def _decide(req_id: int, principal: AnyPrincipal, ctx: Context, *, approve: bool,
            body: dict):
    """approve/reject 的公共路径：状态机校验 + 租户校验 + 落授权。"""
    with ctx.session_factory() as session:
        req = session.get(GrantRequest, req_id)
        if req is None:
            raise HTTPException(status_code=404, detail="申请单不存在")

        tenant_id = _tenant_id(principal)
        if tenant_id is not None and req.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="申请单不存在")

        ok, why = req.can_transition(STATUS_APPROVED if approve else STATUS_REJECTED)
        if not ok:
            raise HTTPException(status_code=409, detail=why)

        # 不能自批
        if req.applicant_user_id == principal.user.id:
            raise HTTPException(
                status_code=403, detail="不能审批自己提交的申请"
            )

        note = (body.get("note") or "").strip()
        if not approve and not note:
            raise HTTPException(status_code=400, detail="拒绝必须填写理由")

        from datetime import datetime, timezone

        req.decided_by_user_id = principal.user.id
        req.decided_at = datetime.now(timezone.utc)
        req.decision_note = note or None

        if not approve:
            req.status = STATUS_REJECTED
            session.commit()
            return {"id": req.id, "status": req.status}

        # ---- 审批通过：落成授权 ----
        ds = session.get(Dataset, req.dataset_id)
        if ds is None or not ds.enabled:
            raise HTTPException(
                status_code=409, detail="数据集已停用，无法授权"
            )

        # 审批人可改最终权限（收紧或放宽），但必须合法
        final_rules = body.get("row_rules")
        if final_rules is None:
            final_rules = req.requested_row_rules
        final_whitelist = body.get("field_whitelist")
        if final_whitelist is None:
            final_whitelist = req.requested_field_whitelist
        final_days = body.get("days")
        if final_days is None:
            final_days = req.requested_days
        _validate_requested(session, ds, final_rules or [], final_whitelist)

        grant = DatasetGrant(
            tenant_id=req.tenant_id,
            user_id=req.applicant_user_id,
            dataset_id=req.dataset_id,
            row_rules=final_rules or [],
            field_whitelist=final_whitelist,
            status="active",
            expires_at=expires_at_from_days(final_days),
        )
        session.add(grant)
        session.flush()  # 拿到 grant.id 回填到申请单

        req.status = STATUS_APPROVED
        req.granted_id = grant.id
        session.commit()

        _audit_grant(session, principal, "grant.approve", grant, via="request",
                     request_id=req.id)
        return {
            "id": req.id,
            "status": req.status,
            "granted_id": grant.id,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        }


@router.post("/{req_id}/approve")
def approve_request(
    req_id: int,
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """审批通过。可同时调整最终的行级规则/白名单/有效期。"""
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    body = body or {}
    return _decide(req_id, principal, ctx, approve=True, body=body)


@router.post("/{req_id}/reject")
def reject_request(
    req_id: int,
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """审批拒绝。必须填理由（申请人要知道为什么）。"""
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    body = body or {}
    return _decide(req_id, principal, ctx, approve=False, body=body)


@router.post("/{req_id}/cancel")
def cancel_request(
    req_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """申请人撤销自己的 pending 申请。"""
    with ctx.session_factory() as session:
        req = session.get(GrantRequest, req_id)
        if req is None:
            raise HTTPException(status_code=404, detail="申请单不存在")
        if req.applicant_user_id != principal.user.id:
            raise HTTPException(status_code=403, detail="只能撤销自己的申请")
        ok, why = req.can_transition(STATUS_CANCELLED)
        if not ok:
            raise HTTPException(status_code=409, detail=why)
        from datetime import datetime, timezone

        req.status = STATUS_CANCELLED
        req.decided_at = datetime.now(timezone.utc)
        session.commit()
        return {"id": req.id, "status": req.status}


def _audit_grant(session, principal, action: str, grant, *, via: str, **extra) -> None:
    """授权变更记审计。失败不影响审批结果本身。"""
    from app.models import AuditLog

    try:
        session.add(
            AuditLog(
                user_id=principal.user.id,
                channel="web",
                dataset=None,
                action=action,
                row_count=0,
                detail={
                    "via": via,
                    "grant_id": grant.id,
                    "target_user_id": grant.user_id,
                    "dataset_id": grant.dataset_id,
                    "row_rules": grant.row_rules,
                    "field_whitelist": grant.field_whitelist,
                    "expires_at": (
                        grant.expires_at.isoformat() if grant.expires_at else None
                    ),
                    **extra,
                },
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001
        pass
