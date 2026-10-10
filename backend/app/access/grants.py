"""数据集授权判定：用户直授优先于角色授权。"""
from sqlalchemy import false, or_

from app.models import Dataset, DatasetGrant, User


class AdminGrant:
    """管理员的全量访问授权：不裁剪字段、不加行级规则。"""

    field_whitelist = None
    row_rules: list = []


def get_effective_grant(session, user: User, dataset_id: int) -> DatasetGrant | None:
    """返回对该用户生效的授权。用户直授 > 角色授权；都没有返回 None。"""
    user_id = getattr(user, "id", None)
    if user_id is not None:
        direct = (
            session.query(DatasetGrant)
            .filter_by(user_id=user_id, dataset_id=dataset_id)
            .first()
        )
        if direct is not None:
            return direct
    role_id = getattr(user, "role_id", None)
    if role_id is None:
        return None
    return (
        session.query(DatasetGrant)
        .filter_by(role_id=role_id, dataset_id=dataset_id)
        .first()
    )


def resolve_grant(session, user: User, dataset_id: int):
    """在授权基础上叠加管理员全量访问：管理员无显式授权时获得全量权限。"""
    grant = get_effective_grant(session, user, dataset_id)
    if grant is not None:
        return grant
    if getattr(user, "is_admin", False):
        return AdminGrant()
    return None


def query_visible_datasets(session, user: User):
    """「可见数据集」的统一查询构造 —— Web/API/MCP/导出等出口共用同一口径。

    管理员：全部启用数据集；
    其他用户：用户直授 ∪ 角色授权（与 `resolve_grant` 的判定语义一致）。
    返回已按 id 排序的 Query，调用方自行 .all()。
    """
    query = session.query(Dataset).filter(Dataset.enabled.is_(True))
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
        query = query.filter(Dataset.id.in_(subq))
    return query.order_by(Dataset.id)
