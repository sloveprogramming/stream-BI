"""数据集授权判定：用户直授优先于角色授权。"""
from app.models import DatasetGrant, User


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
