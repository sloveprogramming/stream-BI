"""租户管理：建租户、列租户、启停。

## 谁能用

**仅平台超管**（`is_superadmin`）。这是与租户内管理员（`is_admin`）的
关键区别：`is_admin` 管"这个租户内的管理权"，`is_superadmin` 管"跨租户"。

普通管理员带 `X-Tenant-Id` 切换租户会被 403（见 `app/tenancy.py`）。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.models import Tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _require_superadmin(principal: AnyPrincipal) -> None:
    if not getattr(principal.user, "is_superadmin", False):
        raise HTTPException(status_code=403, detail="superadmin only")


@router.get("")
def list_tenants(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """列出租户。

    超管看全部；其他用户只能看到自己所属的那一个（否则租户名本身
    就成了信息泄露）。
    """
    with ctx.session_factory() as session:
        if getattr(principal.user, "is_superadmin", False):
            tenants = session.query(Tenant).order_by(Tenant.id).all()
        else:
            own = getattr(principal.user, "tenant_id", None)
            tenants = (
                [session.get(Tenant, own)] if own is not None else []
            )
        return [
            {
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "enabled": t.enabled,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tenants
            if t is not None
        ]


@router.post("", status_code=201)
def create_tenant(
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """建租户（平台超管）。"""
    _require_superadmin(principal)
    name = (body.get("name") or "").strip()
    slug = (body.get("slug") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if not slug:
        raise HTTPException(status_code=400, detail="slug required")
    # slug 用于 URL 与 X-Tenant-Id 头，必须 ASCII 标识符
    if not slug.isascii() or not slug.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="slug 只能是字母/数字/连字符/下划线（ASCII）",
        )
    if slug == "default":
        raise HTTPException(status_code=400, detail="default 是保留租户名")

    with ctx.session_factory() as session:
        if session.query(Tenant).filter_by(slug=slug).first():
            raise HTTPException(status_code=409, detail="slug exists")
        tenant = Tenant(name=name, slug=slug)
        session.add(tenant)
        session.commit()
        return {"id": tenant.id, "name": tenant.name, "slug": tenant.slug}


@router.patch("/{tenant_id}")
def update_tenant(
    tenant_id: int,
    body: dict,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """改租户（改名 / 停用）。停用后该租户所有用户立即无法使用。"""
    _require_superadmin(principal)
    with ctx.session_factory() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            raise HTTPException(status_code=404, detail="tenant not found")
        if "name" in body:
            name = (body["name"] or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="name 不能为空")
            tenant.name = name
        if "enabled" in body:
            if tenant.slug == "default" and not body["enabled"]:
                raise HTTPException(
                    status_code=400, detail="不能停用 default 租户"
                )
            tenant.enabled = bool(body["enabled"])
        session.commit()
        return {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "enabled": tenant.enabled,
        }
