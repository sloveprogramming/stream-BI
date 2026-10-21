"""授权生命周期：有效期判定 + 状态流转 + 到期过期。

## 核心取舍：过期不删数据，只改生效性

授权到期/被撤销时**不 DELETE**，只把 `status` 改成 `expired` / `revoked`。
两个原因：

1. **审计要能查到"这个人曾经有过什么权限"** —— 删了就只剩一条残缺的
   `grant.revoke` 审计记录，看不到当时的行级规则与字段白名单；
2. 误撤销后能恢复（把 status 改回 active 即可），不用重配规则。

`resolve_grant` 只认 `status == 'active'` 且未过期的授权。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from app.models import DatasetGrant

# 授权状态
GRANT_ACTIVE = "active"
GRANT_EXPIRED = "expired"
GRANT_REVOKED = "revoked"


def is_grant_active(grant, now: datetime | None = None) -> bool:
    """这条授权现在是否生效。

    三条同时满足才算生效：status 是 active、未到 expires_at、行不为空。
    过期判定用 `expires_at <= now`（含等于）：到这一刻即失效，不留一秒窗口。
    """
    if grant is None:
        return False
    if getattr(grant, "status", GRANT_ACTIVE) != GRANT_ACTIVE:
        return False
    expires_at = getattr(grant, "expires_at", None)
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        # 库里存的是 naive datetime 时按 UTC 解释（与 server_default now() 一致）
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > (now or datetime.now(timezone.utc))


def active_grants_query(query):
    """给 DatasetGrant 查询加"只取生效中的授权"条件。

    过期时间既可能在未来也可能已是过去，所以不能只按 expires_at 过滤 ——
    还要看 status。两个条件用 OR 组合："没设过期"或"还没到期"。
    """
    now = datetime.now(timezone.utc)
    return query.filter(
        DatasetGrant.status == GRANT_ACTIVE,
        or_(DatasetGrant.expires_at.is_(None), DatasetGrant.expires_at > now),
    )


def expires_at_from_days(days: int | None, now: datetime | None = None) -> datetime | None:
    """把"有效期天数"换算成 expires_at。days 为空或 <=0 表示永久。"""
    if days is None or int(days) <= 0:
        return None
    base = now or datetime.now(timezone.utc)
    return base + timedelta(days=int(days))


def mark_expired(session, *, now: datetime | None = None) -> list[int]:
    """把已过期的 active 授权批量置为 expired，返回受影响的 id 列表。

    幂等：重复跑不会重复改（已 expired 的不满足 `status == active`）。
    """
    now = now or datetime.now(timezone.utc)
    rows = (
        session.query(DatasetGrant)
        .filter(
            DatasetGrant.status == GRANT_ACTIVE,
            DatasetGrant.expires_at.isnot(None),
            DatasetGrant.expires_at <= now,
        )
        .all()
    )
    ids = [g.id for g in rows]
    for grant in rows:
        grant.status = GRANT_EXPIRED
    if ids:
        session.commit()
    return ids


def revoke(session, grant_id: int, *, note: str = "") -> DatasetGrant | None:
    """撤销一条授权（不删除）。返回被撤销的授权，不存在时返回 None。"""
    grant = session.get(DatasetGrant, grant_id)
    if grant is None:
        return None
    grant.status = GRANT_REVOKED
    session.commit()
    return grant
