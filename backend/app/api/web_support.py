from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import json
from pathlib import Path

from app.api.deps import (
    AnyPrincipal,
    Context,
    get_any_principal,
    get_ctx,
    get_principal,
)
from app.models import ApiKey, Dataset, Role, User
from app.security.apikeys import generate_key, hash_secret

router = APIRouter(tags=["web-support"])

_LABELS_PATH = Path(__file__).resolve().parents[1] / "assistant" / "field_labels.json"
try:
    _FIELD_LABELS = json.loads(_LABELS_PATH.read_text(encoding="utf-8"))
except Exception:  # noqa: BLE001
    _FIELD_LABELS = {}


@router.get("/meta/field-labels")
def field_labels(principal=Depends(get_any_principal)):
    """英文列名 → 中文显示名（来自旺店通官方接口文档）。"""
    return _FIELD_LABELS


class KeyBody(BaseModel):
    name: str | None = None


@router.post("/my/keys", status_code=201)
def create_my_key(
    body: KeyBody,
    principal=Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with ctx.session_factory() as session:
        key_id, secret = generate_key()
        key = ApiKey(
            user_id=principal.user.id,
            key_id=key_id,
            secret_hash=hash_secret(secret),
            name=body.name,
        )
        session.add(key)
        session.commit()
        return {"id": key.id, "key_id": key_id, "secret": secret}


@router.get("/my/keys")
def list_my_keys(
    principal=Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with ctx.session_factory() as session:
        keys = (
            session.query(ApiKey)
            .filter_by(user_id=principal.user.id)
            .order_by(ApiKey.id.desc())
            .all()
        )
        return [
            {
                "id": k.id, "key_id": k.key_id, "name": k.name,
                "enabled": k.enabled,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in keys
        ]


@router.post("/my/keys/{key_db_id}/revoke")
def revoke_my_key(
    key_db_id: int,
    principal=Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    with ctx.session_factory() as session:
        key = session.get(ApiKey, key_db_id)
        if key is None or key.user_id != principal.user.id:
            raise HTTPException(status_code=404, detail="key not found")
        key.enabled = False
        session.commit()
        return {"id": key.id, "enabled": False}


@router.get("/admin/users")
def list_users(
    principal=Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    with ctx.session_factory() as session:
        users = session.query(User).order_by(User.id).all()
        return [
            {
                "id": u.id, "username": u.username, "display_name": u.display_name,
                "role_id": u.role_id, "is_admin": u.is_admin, "enabled": u.enabled,
            }
            for u in users
        ]


@router.get("/admin/roles")
def list_roles(
    principal=Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    with ctx.session_factory() as session:
        roles = session.query(Role).order_by(Role.id).all()
        return [{"id": r.id, "name": r.name, "description": r.description} for r in roles]


@router.get("/admin/datasets")
def list_datasets_admin(
    principal=Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    with ctx.session_factory() as session:
        ds = session.query(Dataset).order_by(Dataset.id).all()
        return [
            {
                "id": d.id, "name": d.name, "source_table": d.source_table,
                "title": d.title,
                "pk_columns": d.pk_columns, "columns": d.columns,
                "description": d.description, "enabled": d.enabled,
            }
            for d in ds
        ]
