import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text

from app.access.grants import query_visible_datasets, resolve_grant
from app.access.scoped_query import build_count_sql, build_scoped_sql, check_ident
from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.grants import active_grants_query
from app.models import (
    ApiKey,
    AuditLog,
    Dataset,
    DatasetGrant,
    ReportRun,
    ReportTask,
    Role,
    User,
)
from app.security.apikeys import generate_key, hash_secret
from app.security.passwords import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

_VALID_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "like"}


def _require_admin(principal: AnyPrincipal) -> None:
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")


def _tenant_id(principal: AnyPrincipal) -> int | None:
    """当前请求的租户。None 表示超管要全量（不带租户过滤）。

    新建记录时把它写进 tenant_id —— 漏填的记录在按租户查询时会"消失"，
    这是多租户最常见的静默 bug。
    """
    return getattr(principal.tenant, "tenant_id", None)


@router.post("/users", status_code=201)
def create_user(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    if not body.get("username") or not body.get("password"):
        raise HTTPException(status_code=400, detail="username/password required")
    with ctx.session_factory() as session:
        if session.query(User).filter_by(username=body["username"]).first():
            raise HTTPException(status_code=409, detail="username exists")
        if body.get("role_id") and not session.get(Role, body["role_id"]):
            raise HTTPException(status_code=400, detail="role not found")
        user = User(
            username=body["username"],
            password_hash=hash_password(body["password"]),
            display_name=body.get("display_name"),
            role_id=body.get("role_id"),
            is_admin=bool(body.get("is_admin", False)),
            # 归属当前租户：漏填会让这个用户在按租户查询时消失
            tenant_id=_tenant_id(principal),
        )
        session.add(user)
        session.commit()
        return {"id": user.id, "username": user.username}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """删除用户，并清理其 API Key / 数据集直授 / 报表任务。

    审计日志不删除：解除外键引用（user_id、api_key_id 置空）并在 detail 里记录被删用户名，
    保留完整审计链。保护：不能删除当前登录账号（请求方本身必须是管理员，因此系统始终至少留一个管理员）。
    """
    _require_admin(principal)
    with ctx.session_factory() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if user.id == principal.user.id:
            raise HTTPException(status_code=400, detail="不能删除当前登录账号")

        username = user.username
        key_ids = [
            row[0] for row in session.query(ApiKey.id).filter_by(user_id=user.id).all()
        ]
        if key_ids:
            session.query(AuditLog).filter(AuditLog.api_key_id.in_(key_ids)).update(
                {"api_key_id": None}, synchronize_session=False
            )
        for log in session.query(AuditLog).filter_by(user_id=user.id).all():
            log.user_id = None
            detail = dict(log.detail or {})
            detail["deleted_user"] = username
            log.detail = detail

        task_ids = [
            row[0]
            for row in session.query(ReportTask.id)
            .filter_by(owner_user_id=user.id)
            .all()
        ]
        if task_ids:
            session.query(ReportRun).filter(
                ReportRun.task_id.in_(task_ids)
            ).delete(synchronize_session=False)
            session.query(ReportTask).filter(
                ReportTask.id.in_(task_ids)
            ).delete(synchronize_session=False)

        session.query(DatasetGrant).filter_by(user_id=user.id).delete(
            synchronize_session=False
        )
        session.query(ApiKey).filter_by(user_id=user.id).delete(
            synchronize_session=False
        )
        session.delete(user)
        session.commit()

    # 摘掉该用户残留的报表定时任务（失败不影响删除结果）
    try:
        from app.reports.scheduler import refresh_report_jobs

        refresh_report_jobs(request.app.state.scheduler, ctx.session_factory)
    except Exception:  # noqa: BLE001
        pass

    return {
        "deleted": user_id,
        "username": username,
        "removed": {"api_keys": len(key_ids), "report_tasks": len(task_ids)},
    }


@router.post("/sync/discover")
def discover_erp_tables(
    request: Request,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """手动触发一次 ERP 维护：补齐已有任务的新字段 + 为新表建同步任务，并立即拉一次。"""
    _require_admin(principal)
    from app.sync.erp_autosync import discover_sync_tasks
    from app.sync.evolution import evolve_sync_schemas

    evolved = evolve_sync_schemas(
        ctx.erp_engine, ctx.pg_engine, ctx.session_factory, ctx.settings.discovery_schema
    )
    result = discover_sync_tasks(ctx.erp_engine, ctx.session_factory, ctx.settings)

    task_ids = [item["task_id"] for item in evolved["evolved"]] + [
        item["id"] for item in result["created"]
    ]
    if task_ids:
        from datetime import datetime, timedelta, timezone

        from apscheduler.triggers.date import DateTrigger

        from app.scheduler import execute_task, refresh_jobs

        try:
            scheduler = request.app.state.scheduler
            refresh_jobs(scheduler, ctx.session_factory)
            for task_id in dict.fromkeys(task_ids):
                scheduler.add_job(
                    execute_task,
                    DateTrigger(
                        run_date=datetime.now(timezone.utc) + timedelta(seconds=1)
                    ),
                    args=[task_id, "auto"],
                    id=f"autosync_first_{task_id}",
                    replace_existing=True,
                )
        except Exception:  # noqa: BLE001 —— 任务已建好，调度装载失败不致命
            pass

    return {
        "database": result["database"],
        "erp_tables": result["erp_tables"],
        "created": result["created"],
        "skipped_no_pk": result["skipped_no_pk"],
        "capped": result["capped"],
        "evolved": evolved["evolved"],
        "warnings": evolved["warnings"],
    }


@router.post("/roles", status_code=201)
def create_role(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        if session.query(Role).filter_by(name=body["name"]).first():
            raise HTTPException(status_code=409, detail="role exists")
        role = Role(name=body["name"], description=body.get("description"))
        session.add(role)
        session.commit()
        return {"id": role.id, "name": role.name}


@router.post("/datasets", status_code=201)
def create_dataset(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    source_table = body.get("source_table", "")
    parts = source_table.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=400, detail="source_table 必须是 ods.<table>")
    try:
        for p in parts:
            check_ident(p)
        for c in body.get("columns", []):
            check_ident(c)
        for c in body.get("pk_columns", []):
            check_ident(c)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    with ctx.session_factory() as session:
        if session.query(Dataset).filter_by(name=body["name"]).first():
            raise HTTPException(status_code=409, detail="dataset exists")
        try:
            with ctx.pg_engine.connect() as conn:
                conn.execute(text(f"SELECT 1 FROM {source_table} LIMIT 1"))
        except Exception:
            raise HTTPException(status_code=400, detail="源表不存在")
        ds = Dataset(
            name=body["name"],
            title=body.get("title"),
            source_table=source_table,
            pk_columns=body["pk_columns"],
            columns=body["columns"],
            description=body.get("description"),
            tenant_id=_tenant_id(principal),
        )
        session.add(ds)
        session.commit()
        return {"id": ds.id, "name": ds.name}


@router.post("/datasets/discover")
def discover_datasets_now(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """手动触发一次 ods 新表扫描并注册为数据集。"""
    _require_admin(principal)
    from app.sync.discovery import discover_datasets

    created = discover_datasets(
        ctx.session_factory, ctx.pg_engine, ctx.settings.discovery_schema
    )
    return {"count": len(created), "created": created}


@router.patch("/datasets/{dataset_id}")
def update_dataset(
    dataset_id: int,
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        ds = session.get(Dataset, dataset_id)
        if ds is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        if "title" in body:
            ds.title = body["title"] or None
        if "description" in body:
            ds.description = body["description"] or None
        session.commit()
        return {"id": ds.id, "title": ds.title, "description": ds.description}


@router.post("/grants", status_code=201)
def create_grant(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        tenant_id = _tenant_id(principal)
        ds = session.get(Dataset, body.get("dataset_id"))
        if ds is None:
            raise HTTPException(status_code=400, detail="dataset not found")
        # 跨租户授权一律拒绝：授权记录本身不该跨租户存在
        if tenant_id is not None and ds.tenant_id != tenant_id:
            raise HTTPException(status_code=400, detail="dataset not found")
        role = session.get(Role, body.get("role_id")) if body.get("role_id") else None
        target_user = (
            session.get(User, body.get("user_id")) if body.get("user_id") else None
        )
        if body.get("role_id") and role is None:
            raise HTTPException(status_code=400, detail="role not found")
        if body.get("user_id") and target_user is None:
            raise HTTPException(status_code=400, detail="user not found")
        if role is None and target_user is None:
            raise HTTPException(
                status_code=400, detail="role_id 与 user_id 必须提供一个"
            )
        # 被授权对象也必须同租户（跨租户授权 = 数据泄露）
        if tenant_id is not None and target_user is not None:
            if target_user.tenant_id != tenant_id:
                raise HTTPException(status_code=400, detail="user not found")
        whitelist = body.get("field_whitelist")
        if whitelist is not None:
            bad = set(whitelist) - set(ds.columns)
            if bad:
                raise HTTPException(status_code=400, detail=f"白名单字段不存在: {sorted(bad)}")
        rules = body.get("row_rules") or []
        for rule in rules:
            if rule.get("field") not in ds.columns:
                raise HTTPException(status_code=400, detail=f"规则字段不存在: {rule.get('field')}")
            if rule.get("op") not in _VALID_OPS:
                raise HTTPException(status_code=400, detail=f"非法操作符: {rule.get('op')}")

        # 有效期（天）。空 = 永久。到期由 app/grants.expiry 定时置 expired。
        days = body.get("days")
        expires_at = None
        if days is not None:
            try:
                days = int(days)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="days 必须是整数")
            if days <= 0:
                raise HTTPException(status_code=400, detail="days 必须大于 0")
            from app.grants import expires_at_from_days

            expires_at = expires_at_from_days(days)

        dup_q = active_grants_query(session.query(DatasetGrant)).filter_by(
            dataset_id=ds.id
        )
        dup_q = (
            dup_q.filter_by(role_id=role.id)
            if role is not None
            else dup_q.filter_by(user_id=target_user.id)
        )
        if dup_q.first():
            raise HTTPException(status_code=409, detail="grant exists")
        grant = DatasetGrant(
            role_id=role.id if role is not None else None,
            user_id=target_user.id if target_user is not None else None,
            dataset_id=ds.id,
            row_rules=rules,
            field_whitelist=whitelist,
            status="active",
            expires_at=expires_at,
            tenant_id=tenant_id if tenant_id is not None else ds.tenant_id,
        )
        session.add(grant)
        session.commit()
        # 授权变更记审计（detail 存规则快照：事后能还原"当时开的是什么权限"）
        try:
            session.add(
                AuditLog(
                    user_id=principal.user.id,
                    channel="web",
                    dataset=None,
                    action="grant.create",
                    row_count=0,
                    detail={
                        "grant_id": grant.id,
                        "target_user_id": grant.user_id,
                        "target_role_id": grant.role_id,
                        "dataset_id": grant.dataset_id,
                        "row_rules": grant.row_rules,
                        "field_whitelist": grant.field_whitelist,
                        "expires_at": (
                            grant.expires_at.isoformat() if grant.expires_at else None
                        ),
                    },
                )
            )
            session.commit()
        except Exception:  # noqa: BLE001 —— 审计失败不影响授权本身
            pass
        return {
            "id": grant.id,
            "target": ("role:" + role.name) if role else ("user:" + target_user.username),
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        }


@router.post("/api-keys", status_code=201)
def create_api_key(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        user = session.get(User, body.get("user_id"))
        if user is None:
            raise HTTPException(status_code=400, detail="user not found")
        key_id, secret = generate_key()
        key = ApiKey(
            user_id=user.id,
            key_id=key_id,
            secret_hash=hash_secret(secret),
            name=body.get("name"),
        )
        session.add(key)
        session.commit()
        return {"id": key.id, "key_id": key_id, "secret": secret}


@router.post("/api-keys/{key_id_num}/revoke")
def revoke_api_key(
    key_id_num: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        key = session.get(ApiKey, key_id_num)
        if key is None:
            raise HTTPException(status_code=404, detail="api key not found")
        key.enabled = False
        session.commit()
        return {"id": key.id, "enabled": key.enabled}


@router.get("/audit-logs")
def list_audit_logs(
    limit: int = Query(50, le=200),
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        rows = session.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
        return [
            {
                "id": a.id,
                "user_id": a.user_id,
                "api_key_id": a.api_key_id,
                "channel": a.channel,
                "dataset": a.dataset,
                "action": a.action,
                "row_count": a.row_count,
                "duration_ms": a.duration_ms,
                "detail": a.detail,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ]


@router.get("/grants")
def list_grants(
    dataset_id: int = Query(...),
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """列出一个数据集的全部授权，**含已过期/已撤销的**。

    刻意不按 active 过滤：管理界面要让管理员看到"这个人曾经有过权限"
    （审计需要），由前端把非 active 的标灰。
    """
    _require_admin(principal)
    tenant_id = _tenant_id(principal)
    with ctx.session_factory() as session:
        ds = session.get(Dataset, dataset_id)
        if ds is None:
            raise HTTPException(status_code=404, detail="dataset not found")
        if tenant_id is not None and ds.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="dataset not found")
        rows = (
            session.query(DatasetGrant)
            .filter_by(dataset_id=dataset_id)
            .order_by(DatasetGrant.id)
            .all()
        )
        result = []
        for g in rows:
            role = session.get(Role, g.role_id) if g.role_id else None
            user = session.get(User, g.user_id) if g.user_id else None
            result.append(
                {
                    "id": g.id,
                    "target": ("role:" + role.name) if role else ("user:" + user.username),
                    "row_rules": g.row_rules,
                    "field_whitelist": g.field_whitelist,
                    # 生效性：前端据此标灰；过期/撤销的授权不参与取数
                    "status": g.status,
                    "expires_at": (
                        g.expires_at.isoformat() if g.expires_at else None
                    ),
                }
            )
        return result


@router.delete("/grants/{grant_id}")
def delete_grant(
    grant_id: int,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """撤销授权。**不删记录**，只把 status 改成 revoked ——
    审计要能查到"这个人曾经有过什么权限"，删了就只剩一条空记录。
    """
    _require_admin(principal)
    tenant_id = _tenant_id(principal)
    with ctx.session_factory() as session:
        g = session.get(DatasetGrant, grant_id)
        if g is None:
            raise HTTPException(status_code=404, detail="grant not found")
        if tenant_id is not None and g.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="grant not found")
        from app.grants import GRANT_REVOKED

        g.status = GRANT_REVOKED
        session.commit()
        # 授权变更必须记审计（合规审计最常查的一类）
        try:
            session.add(
                AuditLog(
                    user_id=principal.user.id,
                    channel="web",
                    dataset=None,
                    action="grant.revoke",
                    row_count=0,
                    detail={
                        "grant_id": g.id,
                        "target_user_id": g.user_id,
                        "dataset_id": g.dataset_id,
                        "row_rules": g.row_rules,
                        "field_whitelist": g.field_whitelist,
                    },
                )
            )
            session.commit()
        except Exception:  # noqa: BLE001 —— 审计失败不影响撤销本身
            pass
        return {"deleted": grant_id, "status": g.status}
