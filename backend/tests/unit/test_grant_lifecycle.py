"""权限有效期与申请审批流。

三条要验证的安全语义（每条都对应一个真实事故场景）：

1. **过期即失效**：到点的授权不能再用（哪怕 status 还是 active）；
2. **不能自批**：申请人不能审批自己提的申请（否则申请流形同虚设）；
3. **拒绝必须说明理由**：申请人要知道为什么被拒。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.grants import (
    GRANT_ACTIVE,
    GRANT_EXPIRED,
    GRANT_REVOKED,
    active_grants_query,
    expires_at_from_days,
    is_grant_active,
    mark_expired,
    revoke,
)
from app.main import create_app
from app.models import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Dataset,
    DatasetGrant,
    GrantRequest,
    User,
)


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
    # TestClient 不跑 lifespan，所以 app.state 上没有 engine；
    # 测试夹具工具要用它建源表，这里显式挂上。
    app.state.pg_engine = pg_engine
    app.state.session_factory = pg_session_factory
    return TestClient(app)


# ---------------------------------------------------------------- 有效期纯逻辑


class _G:
    def __init__(self, status=GRANT_ACTIVE, expires_at=None):
        self.status = status
        self.expires_at = expires_at


def test_no_expiry_is_active():
    assert is_grant_active(_G()) is True


def test_future_expiry_is_active():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    assert is_grant_active(_G(expires_at=future)) is True


def test_past_expiry_is_inactive():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert is_grant_active(_G(expires_at=past)) is False


def test_exact_expiry_moment_is_inactive():
    """到这一刻即失效，不留一秒窗口。"""
    now = datetime.now(timezone.utc)
    assert is_grant_active(_G(expires_at=now), now=now) is False


def test_revoked_is_inactive_even_without_expiry():
    assert is_grant_active(_G(status=GRANT_REVOKED)) is False


def test_expired_status_is_inactive():
    assert is_grant_active(_G(status=GRANT_EXPIRED)) is False


def test_naive_datetime_treated_as_utc():
    """库里存 naive datetime 时按 UTC 解释（与 server_default now() 一致）。"""
    naive_future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    assert is_grant_active(_G(expires_at=naive_future)) is True


def test_expires_at_from_days():
    assert expires_at_from_days(None) is None
    assert expires_at_from_days(0) is None
    assert expires_at_from_days(-3) is None
    out = expires_at_from_days(7)
    assert out is not None
    delta = out - datetime.now(timezone.utc)
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, minutes=1)


# ---------------------------------------------------------------- 数据库行为


def test_mark_expired_flips_status(pg_session_factory, pg_engine):
    """到期任务把 status 写实，且幂等。"""
    suffix = uuid.uuid4().hex[:8]
    ids = _mk_dataset(pg_session_factory, pg_engine, suffix, rows=1)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    with pg_session_factory() as s:
        g = DatasetGrant(user_id=ids["user"], dataset_id=ids["ds"], row_rules=[],
                         status=GRANT_ACTIVE, expires_at=past)
        s.add(g)
        s.commit()
        gid = g.id

    try:
        with pg_session_factory() as s:
            changed = mark_expired(s)
            assert gid in changed
            assert s.get(DatasetGrant, gid).status == GRANT_EXPIRED
        # 幂等：再跑一次不该重复改
        with pg_session_factory() as s:
            assert mark_expired(s) == []
    finally:
        _cleanup(pg_session_factory, ids)


def test_active_query_excludes_expired_and_revoked(pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    ids = _mk_dataset(pg_session_factory, pg_engine, suffix, rows=1)
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    with pg_session_factory() as s:
        s.add(DatasetGrant(user_id=ids["user"], dataset_id=ids["ds"], row_rules=[],
                           status=GRANT_ACTIVE, expires_at=None))           # 生效
        s.commit()
        live = active_grants_query(
            s.query(DatasetGrant).filter_by(user_id=ids["user"])
        ).all()
        assert len(live) == 1

        live[0].expires_at = past
        s.commit()
        assert active_grants_query(
            s.query(DatasetGrant).filter_by(user_id=ids["user"])
        ).all() == []

        live[0].expires_at = None
        live[0].status = GRANT_REVOKED
        s.commit()
        assert active_grants_query(
            s.query(DatasetGrant).filter_by(user_id=ids["user"])
        ).all() == []
    _cleanup(pg_session_factory, ids)


def test_revoke_keeps_the_row(pg_session_factory, pg_engine):
    """撤销不删数据：审计要能查到"曾经有过什么权限"。"""
    suffix = uuid.uuid4().hex[:8]
    ids = _mk_dataset(pg_session_factory, pg_engine, suffix, rows=1)
    with pg_session_factory() as s:
        g = DatasetGrant(user_id=ids["user"], dataset_id=ids["ds"], row_rules=[],
                         status=GRANT_ACTIVE)
        s.add(g)
        s.commit()
        gid = g.id
        out = revoke(s, gid)
        assert out is not None and out.status == GRANT_REVOKED
        # 行还在
        assert s.get(DatasetGrant, gid) is not None
    _cleanup(pg_session_factory, ids)


def test_grant_with_days_gets_expires_at(api_client, pg_session_factory, pg_engine):
    """建授权时带 days，落库要有 expires_at。"""
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"gadm_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        r = api_client.post(
            "/api/v1/admin/grants",
            json={"user_id": created["user"], "dataset_id": created["ds"],
                  "row_rules": [], "days": 30},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        assert r.json()["expires_at"] is not None
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created)


def test_grant_rejects_bad_days(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"gadm2_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        for bad in (0, -1, "abc"):
            r = api_client.post(
                "/api/v1/admin/grants",
                json={"user_id": created["user"], "dataset_id": created["ds"],
                      "row_rules": [], "days": bad},
                headers=headers,
            )
            assert r.status_code == 400, f"days={bad} 应被拒绝：{r.text}"
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created)


# ---------------------------------------------------------------- 申请流


def test_request_requires_reason(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"radm_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/grant-requests",
                            json={"dataset_id": created["ds"]}, headers=headers)
        assert r.status_code == 400
        r = api_client.post("/api/v1/grant-requests",
                            json={"dataset_id": created["ds"], "reason": "短"},
                            headers=headers)
        assert r.status_code == 400
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created)


def test_cannot_approve_own_request(api_client, pg_session_factory, pg_engine):
    """管理员申请再自批 = 申请流形同虚设，必须拒绝。"""
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"radm2_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        # 建一个普通用户，用它提交申请
        r = api_client.post("/api/v1/admin/users",
                            json={"username": f"ru_{suffix}", "password": "pw123456"},
                            headers=headers)
        uid = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={"user_id": uid}, headers=headers)
        kb = r.json()
        uheaders = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}

        r = api_client.post("/api/v1/grant-requests",
                            json={"dataset_id": created["ds"], "reason": "需要看华东数据"},
                            headers=uheaders)
        assert r.status_code == 201, r.text
        req_id = r.json()["id"]

        # 申请人自己批自己的
        r = api_client.post(f"/api/v1/grant-requests/{req_id}/approve",
                            json={}, headers=uheaders)
        assert r.status_code == 403, r.text
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created,
                     extra_users=[f"ru_{suffix}"])


def test_reject_requires_note(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"radm3_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/users",
                            json={"username": f"ru2_{suffix}", "password": "pw123456"},
                            headers=headers)
        uid = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={"user_id": uid}, headers=headers)
        kb = r.json()
        uheaders = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}

        r = api_client.post("/api/v1/grant-requests",
                            json={"dataset_id": created["ds"], "reason": "业务上需要这批数据做月度对账"},
                            headers=uheaders)
        req_id = r.json()["id"]

        # 管理员拒绝但不填理由 → 400
        r = api_client.post(f"/api/v1/grant-requests/{req_id}/reject",
                            json={}, headers=headers)
        assert r.status_code == 400, r.text
        # 填了理由 → 成功
        r = api_client.post(f"/api/v1/grant-requests/{req_id}/reject",
                            json={"note": "该数据属财务口径，请走财务审批"},
                            headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == STATUS_REJECTED
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created,
                     extra_users=[f"ru2_{suffix}"])


def test_approve_creates_grant_with_expiry(api_client, pg_session_factory, pg_engine):
    """审批通过要落成授权，且带上申请里声明的有效期。"""
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"radm4_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/users",
                            json={"username": f"ru3_{suffix}", "password": "pw123456"},
                            headers=headers)
        uid = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={"user_id": uid}, headers=headers)
        kb = r.json()
        uheaders = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}

        r = api_client.post("/api/v1/grant-requests",
                            json={"dataset_id": created["ds"], "reason": "需要看华东",
                                  "days": 15,
                                  "row_rules": [{"field": "v", "op": "eq", "value": "a"}]},
                            headers=uheaders)
        assert r.status_code == 201, r.text
        req_id = r.json()["id"]

        r = api_client.post(f"/api/v1/grant-requests/{req_id}/approve",
                            json={"note": "同意"}, headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == STATUS_APPROVED
        assert body["granted_id"] and body["expires_at"]

        with pg_session_factory() as s:
            g = s.get(DatasetGrant, body["granted_id"])
            assert g is not None and g.user_id == uid
            assert g.status == GRANT_ACTIVE
            assert g.expires_at is not None
            assert g.row_rules and g.row_rules[0]["value"] == "a"
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created,
                     extra_users=[f"ru3_{suffix}"])


def test_cancel_by_applicant(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"radm5_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/users",
                            json={"username": f"ru4_{suffix}", "password": "pw123456"},
                            headers=headers)
        uid = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={"user_id": uid}, headers=headers)
        kb = r.json()
        uheaders = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}

        r = api_client.post("/api/v1/grant-requests",
                            json={"dataset_id": created["ds"], "reason": "先申请看看"},
                            headers=uheaders)
        req_id = r.json()["id"]
        r = api_client.post(f"/api/v1/grant-requests/{req_id}/cancel", headers=uheaders)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == STATUS_CANCELLED
        # 已撤销的不能再批
        r = api_client.post(f"/api/v1/grant-requests/{req_id}/approve",
                            json={}, headers=headers)
        assert r.status_code == 409, r.text
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created,
                     extra_users=[f"ru4_{suffix}"])


def test_duplicate_pending_request_rejected(api_client, pg_session_factory, pg_engine):
    """同一数据集只允许一条 pending 申请，避免刷屏。"""
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"radm6_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/users",
                            json={"username": f"ru5_{suffix}", "password": "pw123456"},
                            headers=headers)
        uid = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={"user_id": uid}, headers=headers)
        kb = r.json()
        uheaders = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}

        payload = {"dataset_id": created["ds"], "reason": "第一次申请这个数据集"}
        assert api_client.post("/api/v1/grant-requests", json=payload,
                               headers=uheaders).status_code == 201
        r = api_client.post("/api/v1/grant-requests", json=payload, headers=uheaders)
        assert r.status_code == 409, r.text
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created,
                     extra_users=[f"ru5_{suffix}"])


def test_request_flow_ends_when_already_granted(api_client, pg_session_factory, pg_engine):
    """已有权限的人不该再申请。"""
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"radm7_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/users",
                            json={"username": f"ru6_{suffix}", "password": "pw123456"},
                            headers=headers)
        uid = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={"user_id": uid}, headers=headers)
        kb = r.json()
        uheaders = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}

        api_client.post("/api/v1/admin/grants",
                        json={"user_id": uid, "dataset_id": created["ds"], "row_rules": []},
                        headers=headers)
        r = api_client.post("/api/v1/grant-requests",
                            json={"dataset_id": created["ds"], "reason": "已有权限后再次申请同一数据集"},
                            headers=uheaders)
        assert r.status_code == 409, r.text
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created,
                     extra_users=[f"ru6_{suffix}"])


def test_state_machine_rejects_double_approve(api_client, pg_session_factory, pg_engine):
    """已批准的申请不能重复批准。"""
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"radm8_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk_dataset_via_api(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/users",
                            json={"username": f"ru7_{suffix}", "password": "pw123456"},
                            headers=headers)
        uid = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={"user_id": uid}, headers=headers)
        kb = r.json()
        uheaders = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}

        r = api_client.post("/api/v1/grant-requests",
                            json={"dataset_id": created["ds"], "reason": "申请一次用于测试状态机"},
                            headers=uheaders)
        req_id = r.json()["id"]
        assert api_client.post(f"/api/v1/grant-requests/{req_id}/approve",
                               json={}, headers=headers).status_code == 200
        r = api_client.post(f"/api/v1/grant-requests/{req_id}/approve",
                            json={}, headers=headers)
        assert r.status_code == 409, r.text
    finally:
        _cleanup_api(api_client, pg_session_factory, headers, created,
                     extra_users=[f"ru7_{suffix}"])


# ---------------------------------------------------------------- 夹具工具


def _mk_dataset(session_factory, pg_engine, suffix, *, rows=2):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.gl_t (id integer PRIMARY KEY, v text)"
        ))
    with session_factory() as s:
        u = User(username=f"glu_{suffix}", password_hash="x")
        ds = Dataset(name=f"gl_{suffix}", source_table="ods.gl_t",
                     pk_columns=["id"], columns=["id", "v"])
        s.add_all([u, ds])
        s.commit()
        return {"user": u.id, "ds": ds.id, "uname": u.username, "dsname": ds.name}


def _cleanup(session_factory, ids):
    with session_factory() as s:
        s.query(DatasetGrant).filter_by(dataset_id=ids["ds"]).delete(
            synchronize_session=False
        )
        s.query(GrantRequest).filter_by(dataset_id=ids["ds"]).delete(
            synchronize_session=False
        )
        s.query(Dataset).filter_by(id=ids["ds"]).delete(synchronize_session=False)
        s.query(User).filter_by(id=ids["user"]).delete(synchronize_session=False)
        s.commit()


def _mk_dataset_via_api(client, headers, suffix):
    """用管理接口造一个源表 + 数据集 + 一个普通用户，返回它们的 id。

    走接口而不是直接写库：这样造出来的记录一定带对了租户（
    直写库容易漏 tenant_id，那是我们要测的东西，不能自己先漏）。
    """
    with client.app.state.pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.gl_t "
            "(id integer PRIMARY KEY, v text)"
        ))
        conn.execute(sa.text(
            "INSERT INTO ods.gl_t VALUES (1,'a'),(2,'b') ON CONFLICT (id) DO NOTHING"
        ))

    r = client.post(
        "/api/v1/admin/datasets",
        json={"name": f"gl_{suffix}", "source_table": "ods.gl_t",
              "pk_columns": ["id"], "columns": ["id", "v"]},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    ds_id = r.json()["id"]

    r = client.post(
        "/api/v1/admin/users",
        json={"username": f"glu_{suffix}", "password": "pw123456"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]
    return {"ds": ds_id, "user": user_id, "suffix": suffix}


def _cleanup_api(client, session_factory, headers, created, extra_users=()):
    with session_factory() as s:
        # 先删申请单：它的 granted_id 指向 dataset_grants，顺序反了会撞外键
        s.query(GrantRequest).filter_by(dataset_id=created["ds"]).delete(
            synchronize_session=False
        )
        s.query(DatasetGrant).filter_by(dataset_id=created["ds"]).delete(
            synchronize_session=False
        )
        # 建出来的普通用户（含临时申请专用账号）
        names = [f"glu_{created['suffix']}", *extra_users]
        for uname in names:
            u = s.query(User).filter_by(username=uname).first()
            if u is not None:
                s.query(GrantRequest).filter_by(applicant_user_id=u.id).delete(
                    synchronize_session=False
                )
                s.query(DatasetGrant).filter_by(user_id=u.id).delete(
                    synchronize_session=False
                )
                from app.models import ApiKey

                s.query(ApiKey).filter_by(user_id=u.id).delete(
                    synchronize_session=False
                )
                s.query(User).filter_by(id=u.id).delete(synchronize_session=False)
        s.query(Dataset).filter_by(id=created["ds"]).delete(synchronize_session=False)
        s.commit()
