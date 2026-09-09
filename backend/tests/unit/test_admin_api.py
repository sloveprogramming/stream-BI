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


@pytest.fixture
def admin_headers(pg_session_factory):
    username = f"p2admin_{uuid.uuid4().hex[:8]}"
    _, key = bootstrap_admin(username, "pw123456")
    return {"X-API-Key": key}, username


def _cleanup_user(pg_session_factory, username):
    with pg_session_factory() as s:
        u = s.query(User).filter_by(username=username).first()
        if u is not None:
            s.query(ApiKey).filter_by(user_id=u.id).delete()
            s.query(AuditLog).filter_by(user_id=u.id).delete()
            s.delete(u)
            s.commit()


def test_admin_lifecycle(api_client, pg_session_factory, pg_engine, admin_headers):
    headers, admin_username = admin_headers
    suffix = uuid.uuid4().hex[:8]
    try:
        # ① 创建角色
        r = api_client.post("/api/v1/admin/roles", json={"name": f"east_{suffix}"}, headers=headers)
        assert r.status_code == 201, r.text
        role_id = r.json()["id"]
        # ② 建真实 ods 表 → 注册数据集
        with pg_engine.begin() as conn:
            conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
            conn.execute(sa.text(
                "CREATE TABLE IF NOT EXISTS ods.sales_order_admin "
                "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
            ))
            conn.execute(sa.text(
                "INSERT INTO ods.sales_order_admin VALUES (1,'华东',10,5),(2,'华北',20,8) "
                "ON CONFLICT (id) DO NOTHING"
            ))
        r = api_client.post("/api/v1/admin/datasets", json={
            "name": f"sales_{suffix}", "source_table": "ods.sales_order_admin",
            "pk_columns": ["id"], "columns": ["id", "region", "amount", "cost"],
        }, headers=headers)
        assert r.status_code == 201, r.text
        ds_id = r.json()["id"]
        # ③ 非法表名 → 400
        r = api_client.post("/api/v1/admin/datasets", json={
            "name": f"evil_{suffix}", "source_table": "ods.x; DROP TABLE users",
            "pk_columns": ["id"], "columns": ["id"],
        }, headers=headers)
        assert r.status_code == 400
        # ④ 创建授权
        r = api_client.post("/api/v1/admin/grants", json={
            "role_id": role_id, "dataset_id": ds_id,
            "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
            "field_whitelist": ["id", "region", "amount"],
        }, headers=headers)
        assert r.status_code == 201, r.text
        # ⑤ 白名单不在列内 → 400
        r = api_client.post("/api/v1/admin/grants", json={
            "role_id": role_id, "dataset_id": ds_id,
            "field_whitelist": ["id", "hacker"],
        }, headers=headers)
        assert r.status_code == 400
        # ⑥ 创建普通用户并绑定角色 → 发 Key
        r = api_client.post("/api/v1/admin/users", json={
            "username": f"p2user_{suffix}", "password": "pw123456", "role_id": role_id,
        }, headers=headers)
        assert r.status_code == 201, r.text
        user_id = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={
            "user_id": user_id, "name": "p2",
        }, headers=headers)
        assert r.status_code == 201, r.text
        key_body = r.json()
        assert key_body["secret"]
        # ⑦ 撤销 Key
        r = api_client.post(f"/api/v1/admin/api-keys/{key_body['id']}/revoke", headers=headers)
        assert r.json()["enabled"] is False
        # ⑧ 非管理员访问 admin → 403（撤销前先测，重发一个）
        r = api_client.post("/api/v1/admin/api-keys", json={
            "user_id": user_id, "name": "p2b",
        }, headers=headers)
        k2 = r.json()
        r = api_client.get("/api/v1/admin/audit-logs", headers={
            "X-API-Key": f"{k2['key_id']}.{k2['secret']}"
        })
        assert r.status_code == 403
        # 清理
        with pg_session_factory() as s:
            s.query(ApiKey).filter_by(user_id=user_id).delete()
            s.query(DatasetGrant).filter_by(role_id=role_id).delete()
            s.query(User).filter_by(id=user_id).delete()
            s.query(Role).filter_by(id=role_id).delete()
            s.query(Dataset).filter_by(id=ds_id).delete()
            s.commit()
        _cleanup_user(pg_session_factory, f"p2user_{suffix}")
    finally:
        _cleanup_user(pg_session_factory, admin_username)

