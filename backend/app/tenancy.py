"""租户上下文：从请求解析出"当前租户"，并提供统一的查询过滤。

## 设计要点

**过滤放在 principal 层，不在每个 API 里手写。**
手写必然漏 —— 漏一处就是跨租户数据泄露。所以 `get_any_principal`
解析出 `TenantContext`，各 API 用它过滤。

**超管可以跨租户。**
`X-Tenant-Id: <slug>` 头只有 `is_superadmin` 能用；其他人带这个头被忽略
（不是报错：忽略比报错更难被发现配置问题，但这里选报错——
静默忽略会让人以为切换成功了）。

**default 租户 = 零回归。**
存量数据全部归 default。没配租户的用户（tenant_id=None）也按 default 处理，
所以升级后行为与单租户时代完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.models import DEFAULT_TENANT_SLUG, Tenant


@dataclass(frozen=True)
class TenantContext:
    """一次请求的租户上下文。

    `tenant_id` 为 None 表示"不按租户过滤"（仅平台超管显式请求全量时）。
    正常请求永远有 tenant_id。
    """

    tenant_id: int | None
    slug: str
    is_superadmin: bool
    # 是否显式指定了租户（超管切换）；False = 用自己归属的租户
    explicit: bool = False

    def filter(self, query, column):
        """给 query 加租户过滤。tenant_id 为 None 时不过滤（超管全量）。"""
        if self.tenant_id is None:
            return query
        return query.filter(column == self.tenant_id)

    def scope(self, value: int | None) -> int | None:
        """给新建的记录定租户：显式指定优先，否则用自己的。"""
        return self.tenant_id if self.tenant_id is not None else value


def resolve_tenant(request: Request, user, session) -> TenantContext:
    """从请求头 + 用户归属解析租户上下文。

    优先级：`X-Tenant-Id`（仅超管）> 用户自己的 `tenant_id` > default 租户。
    """
    user_tenant_id = getattr(user, "tenant_id", None)
    is_super = bool(getattr(user, "is_superadmin", False))

    header_slug = (request.headers.get("X-Tenant-Id") or "").strip()
    if header_slug:
        if not is_super:
            raise HTTPException(
                status_code=403,
                detail="X-Tenant-Id 仅平台超管可用（跨租户切换）",
            )
        tenant = session.query(Tenant).filter_by(slug=header_slug).first()
        if tenant is None:
            raise HTTPException(status_code=404, detail=f"租户不存在: {header_slug}")
        if not tenant.enabled:
            raise HTTPException(status_code=403, detail=f"租户已停用: {header_slug}")
        return TenantContext(
            tenant_id=tenant.id,
            slug=tenant.slug,
            is_superadmin=True,
            explicit=True,
        )

    if user_tenant_id is not None:
        tenant = session.get(Tenant, user_tenant_id)
        if tenant is not None and not tenant.enabled:
            raise HTTPException(status_code=403, detail="所属租户已停用")
        slug = tenant.slug if tenant else DEFAULT_TENANT_SLUG
        return TenantContext(
            tenant_id=user_tenant_id, slug=slug, is_superadmin=is_super
        )

    # 没有归属租户的用户（含存量数据）：落到 default 租户
    tenant = session.query(Tenant).filter_by(slug=DEFAULT_TENANT_SLUG).first()
    return TenantContext(
        tenant_id=tenant.id if tenant else None,
        slug=DEFAULT_TENANT_SLUG,
        is_superadmin=is_super,
    )


def ensure_default_tenant(session) -> Tenant:
    """确保 default 租户存在（迁移与测试都用它）。"""
    tenant = session.query(Tenant).filter_by(slug=DEFAULT_TENANT_SLUG).first()
    if tenant is None:
        tenant = Tenant(name="默认组织", slug=DEFAULT_TENANT_SLUG)
        session.add(tenant)
        session.commit()
    return tenant
