"""租户隔离：跨租户不可见、不能授权、不能借 X-Tenant-Id 越权。

这些断言是租户能力的**安全边界**：每一条都对应一种真实的越权路径。
纯逻辑部分（`_same_tenant` / `TenantContext`）不连库，直接单测；
带库的部分验证"查询真的被过滤了"。
"""
import uuid

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.access.grants import query_visible_datasets, resolve_grant
from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import DEFAULT_TENANT_SLUG, Dataset, DatasetGrant, Tenant, User
from app.tenancy import TenantContext, resolve_tenant


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000
    export_max_rows = 50000
    export_dir = ""


@pytest.fixture
def api_client(pg_session_factory, pg_engine):
    app = create_app()

    def override_ctx(request: Request):
        return Context(
            session_factory=pg_session_factory,
            erp_engine=object(),
            pg_engine=pg_engine,
            settings=FakeSettings(),
        )

    app.dependency_overrides[get_ctx] = override_ctx
    return TestClient(app)


# ---------------------------------------------------------------- 纯逻辑


def test_unassigned_tenant_is_treated_as_same_tenant():
    """任一方没归属租户时按 default 处理 —— 存量数据升级后行为不变。"""

    class U:
        tenant_id = None

    class D:
        tenant_id = 5

    from app.access.grants import _same_tenant

    assert _same_tenant(U(), D()) is True
    assert _same_tenant(U(), type("D2", (), {"tenant_id": None})()) is True


def test_different_tenant_is_not_same_tenant():
    from app.access.grants import _same_tenant

    u = type("U", (), {"tenant_id": 1})()
    d = type("D", (), {"tenant_id": 2})()
    assert _same_tenant(u, d) is False
    d2 = type("D", (), {"tenant_id": 1})()
    assert _same_tenant(u, d2) is True


def test_tenant_context_filter_skips_when_no_tenant():
    """tenant_id 为 None（超管全量）时不加过滤条件。"""
    ctx = TenantContext(tenant_id=None, slug="*", is_superadmin=True)
    sentinel = object()
    assert ctx.filter(sentinel, Dataset.tenant_id) is sentinel


def test_tenant_context_filter_applies_condition():
    ctx = TenantContext(tenant_id=7, slug="t", is_superadmin=False)
    q = ctx.filter(sa.select(Dataset.id), Dataset.tenant_id)
    assert "tenant_id" in str(q)


# ---------------------------------------------------------------- 头部越权


def test_non_superadmin_cannot_use_tenant_header(pg_session_factory):
    """普通用户带 X-Tenant-Id 必须 403 —— 静默忽略会让人以为切换成功了。"""
    from fastapi import HTTPException

    suffix = uuid.uuid4().hex[:8]
    with pg_session_factory() as s:
        other = Tenant(name=f"T{suffix}", slug=f"t{suffix}")
        s.add(other)
        s.commit()
        user = User(username=f"tu_{suffix}", password_hash="x", tenant_id=1)
        s.add(user)
        s.commit()
        try:
            req = _fake_request({"X-Tenant-Id": f"t{suffix}"})
            with pytest.raises(HTTPException) as exc:
                resolve_tenant(req, user, s)
            assert exc.value.status_code == 403
        finally:
            s.query(User).filter_by(id=user.id).delete()
            s.query(Tenant).filter_by(id=other.id).delete()
            s.commit()


def test_superadmin_can_switch_tenant(pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    with pg_session_factory() as s:
        other = Tenant(name=f"T{suffix}", slug=f"t{suffix}")
        s.add(other)
        s.commit()
        admin = User(
            username=f"sa_{suffix}", password_hash="x", is_superadmin=True, tenant_id=1
        )
        s.add(admin)
        s.commit()
        try:
            req = _fake_request({"X-Tenant-Id": f"t{suffix}"})
            ctx = resolve_tenant(req, admin, s)
            assert ctx.tenant_id == other.id
            assert ctx.explicit is True
        finally:
            s.query(User).filter_by(id=admin.id).delete()
            s.query(Tenant).filter_by(id=other.id).delete()
            s.commit()


def test_unknown_tenant_header_is_404(pg_session_factory):
    from fastapi import HTTPException

    with pg_session_factory() as s:
        admin = User(username=f"sa2_{uuid.uuid4().hex[:8]}", password_hash="x",
                     is_superadmin=True, tenant_id=1)
        s.add(admin)
        s.commit()
        try:
            req = _fake_request({"X-Tenant-Id": "no-such-tenant"})
            with pytest.raises(HTTPException) as exc:
                resolve_tenant(req, admin, s)
            assert exc.value.status_code == 404
        finally:
            s.query(User).filter_by(id=admin.id).delete()
            s.commit()


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def _fake_request(headers: dict) -> _FakeRequest:
    return _FakeRequest(headers)


# ---------------------------------------------------------------- 查询过滤


def test_visible_datasets_excludes_other_tenant(pg_session_factory, pg_engine):
    """A 租户的用户看不到 B 租户的数据集（即使有授权记录）。"""
    suffix = uuid.uuid4().hex[:8]
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.tt_t (id integer PRIMARY KEY, v text)"
        ))

    with pg_session_factory() as s:
        tenant_a = s.query(Tenant).filter_by(slug=DEFAULT_TENANT_SLUG).first()
        tenant_b = Tenant(name=f"B{suffix}", slug=f"b{suffix}")
        s.add(tenant_b)
        s.flush()

        user_a = User(username=f"ua_{suffix}", password_hash="x",
                      tenant_id=tenant_a.id)
        s.add(user_a)
        s.flush()

        ds_a = Dataset(name=f"tt_a_{suffix}", source_table="ods.tt_t",
                       pk_columns=["id"], columns=["id", "v"], tenant_id=tenant_a.id)
        ds_b = Dataset(name=f"tt_b_{suffix}", source_table="ods.tt_t",
                       pk_columns=["id"], columns=["id", "v"], tenant_id=tenant_b.id)
        s.add_all([ds_a, ds_b])
        s.flush()
        # 给 A 租户用户授 B 租户数据集（模拟"错误的跨租户授权"）
        s.add(DatasetGrant(user_id=user_a.id, dataset_id=ds_b.id, row_rules=[],
                           tenant_id=tenant_b.id))
        s.add(DatasetGrant(user_id=user_a.id, dataset_id=ds_a.id, row_rules=[],
                           tenant_id=tenant_a.id))
        s.commit()
        ids = {"user": user_a.id, "ds_a": ds_a.id, "ds_b": ds_b.id,
               "tenant_b": tenant_b.id}
        try:
            with pg_session_factory() as s2:
                user = s2.get(User, ids["user"])
                names = {
                    d.name
                    for d in query_visible_datasets(s2, user, tenant_a.id).all()
                }
                assert f"tt_a_{suffix}" in names
                assert f"tt_b_{suffix}" not in names
                # 即使有授权记录，resolve_grant 也拒绝跨租户
                assert resolve_grant(s2, user, ids["ds_b"]) is None
                assert resolve_grant(s2, user, ids["ds_a"]) is not None
        finally:
            with pg_session_factory() as s3:
                s3.query(DatasetGrant).filter(
                    DatasetGrant.dataset_id.in_([ids["ds_a"], ids["ds_b"]])
                ).delete(synchronize_session=False)
                s3.query(Dataset).filter(
                    Dataset.id.in_([ids["ds_a"], ids["ds_b"]])
                ).delete(synchronize_session=False)
                s3.query(User).filter_by(id=ids["user"]).delete()
                s3.query(Tenant).filter_by(id=ids["tenant_b"]).delete()
                s3.commit()


def test_api_rejects_cross_tenant_dataset_name(api_client, pg_session_factory, pg_engine):
    """REST 通道按名字取数据集时也受租户约束，且不泄露"存在但无权"。"""
    suffix = uuid.uuid4().hex[:8]
    _, admin_key = bootstrap_admin(f"tadm_{suffix}", "pw123456")
    headers = {"X-API-Key": admin_key}

    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.xt_t (id integer PRIMARY KEY, v text)"
        ))

    with pg_session_factory() as s:
        other = Tenant(name=f"X{suffix}", slug=f"x{suffix}")
        s.add(other)
        s.flush()
        ds = Dataset(name=f"xt_{suffix}", source_table="ods.xt_t",
                     pk_columns=["id"], columns=["id", "v"], tenant_id=other.id)
        s.add(ds)
        s.commit()
        ds_id, tenant_id = ds.id, other.id

    try:
        # 管理员的租户是 default，看不到 X 租户的数据集 → 404（不是 403，
        # 否则可以用状态码差异探测别人有哪些数据集）
        r = api_client.get(f"/api/v1/datasets/xt_{suffix}/rows", headers=headers)
        assert r.status_code == 404, r.text
    finally:
        with pg_session_factory() as s:
            s.query(Dataset).filter_by(id=ds_id).delete()
            s.query(Tenant).filter_by(id=tenant_id).delete()
            s.commit()


def test_tenant_api_requires_superadmin(api_client, pg_session_factory):
    """租户管理接口只对平台超管开放。"""
    suffix = uuid.uuid4().hex[:8]
    _, admin_key = bootstrap_admin(f"tadm2_{suffix}", "pw123456")
    headers = {"X-API-Key": admin_key}
    # 普通管理员（is_admin=True 但 is_superadmin=False）不能建租户
    r = api_client.post(
        "/api/v1/tenants", json={"name": "N", "slug": f"n{suffix}"}, headers=headers
    )
    assert r.status_code == 403, r.text
