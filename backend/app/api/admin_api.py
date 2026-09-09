import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.access.scoped_query import check_ident
from app.api.deps import Context, Principal, get_ctx, get_principal
from app.models import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User
from app.security.apikeys import generate_key, hash_secret
from app.security.passwords import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

_VALID_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "like"}


def _require_admin(principal: Principal) -> None:
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")


@router.post("/users", status_code=201)
def create_user(
    body: dict,
    principal: Principal = Depends(get_principal),
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
        )
        session.add(user)
        session.commit()
        return {"id": user.id, "username": user.username}


@router.post("/roles", status_code=201)
def create_role(
    body: dict,
    principal: Principal = Depends(get_principal),
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
    principal: Principal = Depends(get_principal),
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
            source_table=source_table,
            pk_columns=body["pk_columns"],
            columns=body["columns"],
            description=body.get("description"),
        )
        session.add(ds)
        session.commit()
        return {"id": ds.id, "name": ds.name}


@router.post("/grants", status_code=201)
def create_grant(
    body: dict,
    principal: Principal = Depends(get_principal),
    ctx: Context = Depends(get_ctx),
):
    _require_admin(principal)
    with ctx.session_factory() as session:
        role = session.get(Role, body.get("role_id"))
        ds = session.get(Dataset, body.get("dataset_id"))
        if role is None or ds is None:
            raise HTTPException(status_code=400, detail="role/dataset not found")
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
        existing = (
            session.query(DatasetGrant)
            .filter_by(role_id=role.id, dataset_id=ds.id)
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail="grant exists")
        grant = DatasetGrant(
            role_id=role.id,
            dataset_id=ds.id,
            row_rules=rules,
            field_whitelist=whitelist,
        )
        session.add(grant)
        session.commit()
        return {"id": grant.id}


@router.post("/api-keys", status_code=201)
def create_api_key(
    body: dict,
    principal: Principal = Depends(get_principal),
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
    principal: Principal = Depends(get_principal),
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
    principal: Principal = Depends(get_principal),
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
