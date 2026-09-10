import pytest

from sqlalchemy.exc import IntegrityError

from app.models import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User


def test_user_and_role_roundtrip(pg_session_factory):
    with pg_session_factory() as s:
        u = User(username="alice", password_hash="x", display_name="Alice")
        r = Role(name="east-sales", description="华东销售")
        s.add_all([u, r])
        s.commit()
        assert u.id is not None and r.id is not None
        s.query(User).filter_by(username="alice").delete()
        s.query(Role).filter_by(name="east-sales").delete()
        s.commit()


def test_duplicate_username_rejected(pg_session_factory):
    with pg_session_factory() as s:
        s.query(User).filter_by(username="bob").delete()
        s.commit()
        s.add(User(username="bob", password_hash="x"))
        s.commit()
        s.add(User(username="bob", password_hash="y"))
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        s.query(User).filter_by(username="bob").delete()
        s.commit()


def test_dataset_grant_role_and_user_targets(pg_session_factory):
    with pg_session_factory() as s:
        grant_role_id = s.query(Role.id).filter_by(name="grant-r").scalar_subquery()
        grant_ds_id = s.query(Dataset.id).filter_by(name="sales_order").scalar_subquery()
        s.query(DatasetGrant).filter(
            (DatasetGrant.role_id == grant_role_id)
            | (DatasetGrant.dataset_id == grant_ds_id)
        ).delete(synchronize_session=False)
        s.query(Role).filter_by(name="grant-r").delete()
        s.query(User).filter_by(username="grant-u").delete()
        s.query(Dataset).filter_by(name="sales_order").delete()
        s.commit()
        r = Role(name="grant-r")
        u = User(username="grant-u", password_hash="x")
        d = Dataset(
            name="sales_order",
            source_table="ods.sales_order",
            pk_columns=["id"],
            columns=["id", "region", "amount"],
            description="销售订单",
        )
        s.add_all([r, u, d])
        s.commit()
        role_grant = DatasetGrant(
            role_id=r.id,
            dataset_id=d.id,
            row_rules=[{"field": "region", "op": "eq", "value": "华东"}],
            field_whitelist=["id", "region"],
        )
        user_grant = DatasetGrant(
            user_id=u.id,
            dataset_id=d.id,
            row_rules=[{"field": "region", "op": "eq", "value": "华北"}],
            field_whitelist=["id", "amount"],
        )
        s.add_all([role_grant, user_grant])
        s.commit()
        assert role_grant.role_id == r.id and role_grant.user_id is None
        assert user_grant.user_id == u.id and user_grant.role_id is None
        s.query(DatasetGrant).filter_by(id=role_grant.id).delete()
        s.query(DatasetGrant).filter_by(id=user_grant.id).delete()
        s.query(Dataset).filter_by(id=d.id).delete()
        s.query(Role).filter_by(id=r.id).delete()
        s.query(User).filter_by(id=u.id).delete()
        s.commit()


def test_api_key_and_audit_roundtrip(pg_session_factory):
    with pg_session_factory() as s:
        u = User(username="carol", password_hash="x")
        d = Dataset(
            name="logistics",
            source_table="ods.logistics",
            pk_columns=["id"],
            columns=["id", "status"],
        )
        s.add_all([u, d])
        s.commit()
        k = ApiKey(user_id=u.id, key_id="bk_abcd1234", secret_hash="h", name="测试")
        s.add(k)
        s.commit()
        a = AuditLog(
            user_id=u.id,
            api_key_id=k.id,
            channel="api",
            dataset="logistics",
            action="query",
            row_count=3,
            duration_ms=12,
            detail={"filters": {}},
        )
        s.add(a)
        s.commit()
        assert a.id is not None
        s.query(AuditLog).filter_by(id=a.id).delete()
        s.query(ApiKey).filter_by(id=k.id).delete()
        s.query(Dataset).filter_by(id=d.id).delete()
        s.query(User).filter_by(id=u.id).delete()
        s.commit()
