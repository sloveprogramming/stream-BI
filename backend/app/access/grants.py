"""数据集授权判定：用户直授优先于角色授权。

三条硬约束在这里落地：

1. **租户隔离**：跨租户的授权一律不认（`resolve_grant` 校验 dataset 与 user
   同租户）。宁可拒，不可漏。
2. **有效期**：`status != active` 或已过 `expires_at` 的授权不生效
   （见 `app/grants.py`）。过期不删数据，只改生效性。
3. **管理员兜底**：`resolve_grant` 对管理员返回全量 `AdminGrant`；
   `get_effective_grant` 不会 —— 用错函数管理员会被 403（README 约定）。
"""
from sqlalchemy import false, or_

from app.grants import active_grants_query
from app.models import Dataset, DatasetGrant, User


class AdminGrant:
    """管理员的全量访问授权：不裁剪字段、不加行级规则。"""

    field_whitelist = None
    row_rules: list = []


def _same_tenant(user: User, dataset: Dataset) -> bool:
    """用户与数据集是否同租户。任一方未归属（tenant_id=None）时按 default 处理。

    这样升级后（存量数据 tenant_id 全有值）行为一致；新插入但漏填
    tenant_id 的记录也不会突然互相不可见。
    """
    user_tenant = getattr(user, "tenant_id", None)
    dataset_tenant = getattr(dataset, "tenant_id", None)
    if user_tenant is None or dataset_tenant is None:
        return True
    return user_tenant == dataset_tenant


def get_effective_grant(session, user: User, dataset_id: int) -> DatasetGrant | None:
    """返回对该用户生效的授权。用户直授 > 角色授权；都没有返回 None。

    只返回**生效中**的授权（active 且未过期）。过期/撤销的授权在管理界面
    仍可见（审计需要），但不参与取数判定。
    """
    user_id = getattr(user, "id", None)
    if user_id is not None:
        direct = (
            active_grants_query(session.query(DatasetGrant))
            .filter_by(user_id=user_id, dataset_id=dataset_id)
            .first()
        )
        if direct is not None:
            return direct
    role_id = getattr(user, "role_id", None)
    if role_id is None:
        return None
    return (
        active_grants_query(session.query(DatasetGrant))
        .filter_by(role_id=role_id, dataset_id=dataset_id)
        .first()
    )


def resolve_grant(session, user: User, dataset_id: int):
    """在授权基础上叠加管理员全量访问：管理员无显式授权时获得全量权限。

    同时做**租户校验**：数据集属于别的租户时，即使有授权记录也不认
    （授权记录本身不该跨租户存在，这里是纵深防御）。
    """
    dataset = session.get(Dataset, dataset_id)
    if dataset is not None and not _same_tenant(user, dataset):
        return None

    grant = get_effective_grant(session, user, dataset_id)
    if grant is not None:
        return grant
    if getattr(user, "is_admin", False):
        return AdminGrant()
    return None


def query_visible_datasets(session, user: User, tenant_id: int | None = None):
    """「可见数据集」的统一查询构造 —— Web/API/MCP/导出等出口共用同一口径。

    管理员：租户内全部启用数据集；
    其他用户：租户内「用户直授 ∪ 角色授权」（与 `resolve_grant` 判定语义一致）。

    `tenant_id` 显式传入时按它过滤（请求的租户上下文）；不传则用用户自己的
    归属租户。返回已按 id 排序的 Query，调用方自行 .all()。
    """
    effective_tenant = (
        tenant_id if tenant_id is not None else getattr(user, "tenant_id", None)
    )
    query = session.query(Dataset).filter(Dataset.enabled.is_(True))
    if effective_tenant is not None:
        query = query.filter(Dataset.tenant_id == effective_tenant)
    if not getattr(user, "is_admin", False):
        conditions = []
        user_id = getattr(user, "id", None)
        if user_id is not None:
            conditions.append(DatasetGrant.user_id == user_id)
        role_id = getattr(user, "role_id", None)
        if role_id is not None:
            conditions.append(DatasetGrant.role_id == role_id)
        subq = session.query(DatasetGrant.dataset_id)
        subq = subq.filter(or_(*conditions)) if conditions else subq.filter(false())
        # 只看生效中的授权：过期的授权不该让数据集继续可见
        subq = active_grants_query(subq)
        query = query.filter(Dataset.id.in_(subq))
    return query.order_by(Dataset.id)
