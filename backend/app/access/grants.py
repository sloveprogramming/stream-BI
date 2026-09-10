"""数据集授权判定：用户直授优先于角色授权。"""
from app.models import DatasetGrant, User


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
