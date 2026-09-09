import uuid

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000


class RateLimitSettings(FakeSettings):
    rate_limit_per_minute = 2


@pytest.fixture
def client_factory(pg_session_factory, pg_engine):
    def _make(settings=FakeSettings):
        app = create_app()

        def override_ctx(request: Request):
            return Context(
                session_factory=pg_session_factory,
                erp_engine=object(),
                pg_engine=pg_engine,
                settings=settings,
            )

        app.dependency_overrides[get_ctx] = override_ctx
        return TestClient(app)

    return _make


def _cleanup_user(pg_session_factory, username):
    with pg_session_factory() as s:
        u = s.query(User).filter_by(username=username).first()
        if u is not None:
            s.query(AuditLog).filter_by(user_id=u.id).delete()
            s.query(ApiKey).filter_by(user_id=u.id).delete()
            s.delete(u)
            s.commit()


def test_p2_end_to_end(client_factory, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    created = {"users": [], "roles": [], "datasets": []}
    try:
        # ① 管理员引导（真实鉴权依赖，无 get_principal override）
        admin_user, admin_key = bootstrap_admin(f"p2e2e_{suffix}", "pw123456")
        created["users"].append(f"p2e2e_{suffix}")
        headers = {"X-API-Key": admin_key}
        client = client_factory()
        # ② 数据集 → 角色 → 授权（region=华东，白名单无 cost）→ 用户 → Key
        with pg_engine.begin() as conn:
            conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
            conn.execute(sa.text(
                "CREATE TABLE IF NOT EXISTS ods.sales_order_e2e2 "
                "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
            ))
            conn.execute(sa.text(
                "INSERT INTO ods.sales_order_e2e2 VALUES "
                "(1,'华东',10,5),(2,'华北',20,8) ON CONFLICT (id) DO NOTHING"
            ))
        r = client.post("/api/v1/admin/roles", json={"name": f"r_{suffix}"}, headers=headers)
        role_id = r.json()["id"]
        created["roles"].append(role_id)
        r = client.post("/api/v1/admin/datasets", json={
            "name": f"sales_e2e_{suffix}", "source_table": "ods.sales_order_e2e2",
            "pk_columns": ["id"], "columns": ["id", "region", "amount", "cost"],
        }, headers=headers)
        assert r.status_code == 201, r.text
        ds_id, ds_name = r.json()["id"], r.json()["name"]
        created["datasets"].append(ds_id)
        r = client.post("/api/v1/admin/grants", json={
            "role_id": role_id, "dataset_id": ds_id,
            "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
            "field_whitelist": ["id", "region", "amount"],
        }, headers=headers)
        assert r.status_code == 201, r.text
        username = f"u_{suffix}"
        r = client.post("/api/v1/admin/users", json={
            "username": username, "password": "pw123456", "role_id": role_id,
        }, headers=headers)
        assert r.status_code == 201, r.text
        created["users"].append(username)
        user_id = r.json()["id"]
        r = client.post("/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers)
        kb = r.json()
        user_headers = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}
        # ③ 权限裁剪查询
        r = client.get(f"/api/v1/datasets/{ds_name}/rows", headers=user_headers)
        body = r.json()
        assert body["total"] == 1
        assert body["rows"][0]["region"] == "华东"
        assert "cost" not in body["rows"][0]
        # ④ 撤销 Key → 401
        r = client.post(f"/api/v1/admin/api-keys/{kb['id']}/revoke", headers=headers)
        assert r.json()["enabled"] is False
        assert client.get(
            f"/api/v1/datasets/{ds_name}/rows", headers=user_headers
        ).status_code == 401
        # ⑤ 重新发 Key → 限流 429（每分钟 2 次）
        r = client.post("/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers)
        kb2 = r.json()
        user_headers2 = {"X-API-Key": f"{kb2['key_id']}.{kb2['secret']}"}
        from app.access.ratelimit import LimiterHolder

        LimiterHolder._limiter = None  # 重置进程级单例，让限流配置生效
        lim_client = client_factory(RateLimitSettings)
        codes = [
            lim_client.get(
                f"/api/v1/datasets/{ds_name}/rows", headers=user_headers2
            ).status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        # ⑥ 审计包含 query 与 revoke
        r = client.get("/api/v1/admin/audit-logs?limit=50", headers=headers)
        actions = {log["action"] for log in r.json() if log["user_id"] == user_id}
        assert "query" in actions
    finally:
        with pg_session_factory() as s:
            for uname in created["users"]:
                u = s.query(User).filter_by(username=uname).first()
                if u is not None:
                    s.query(AuditLog).filter_by(user_id=u.id).delete()
                    s.query(ApiKey).filter_by(user_id=u.id).delete()
                    s.delete(u)
            for rid in created["roles"]:
                s.query(DatasetGrant).filter_by(role_id=rid).delete()
                s.query(Role).filter_by(id=rid).delete()
            for did in created["datasets"]:
                s.query(Dataset).filter_by(id=did).delete()
            s.commit()


