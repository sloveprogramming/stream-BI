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
    username = f"p2data_admin_{uuid.uuid4().hex[:8]}"
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


@pytest.fixture
def scoped_env(api_client, pg_session_factory, pg_engine, admin_headers):
    """建数据集（华东/华北各一行）→ 角色（region=华东，白名单无 cost）→ 用户 → Key"""
    headers, admin_username = admin_headers
    suffix = uuid.uuid4().hex[:8]
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.sales_order_data "
            "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
        ))
        conn.execute(sa.text(
            "INSERT INTO ods.sales_order_data VALUES "
            "(1,'华东',10,5),(2,'华北',20,8) ON CONFLICT (id) DO NOTHING"
        ))
    r = api_client.post("/api/v1/admin/roles", json={"name": f"r_{suffix}"}, headers=headers)
    role_id = r.json()["id"]
    r = api_client.post("/api/v1/admin/datasets", json={
        "name": f"sales_{suffix}", "source_table": "ods.sales_order_data",
        "pk_columns": ["id"], "columns": ["id", "region", "amount", "cost"],
    }, headers=headers)
    ds_id, ds_name = r.json()["id"], r.json()["name"]
    r = api_client.post("/api/v1/admin/grants", json={
        "role_id": role_id, "dataset_id": ds_id,
        "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
        "field_whitelist": ["id", "region", "amount"],
    }, headers=headers)
    assert r.status_code == 201
    r = api_client.post("/api/v1/admin/users", json={
        "username": f"u_{suffix}", "password": "pw123456", "role_id": role_id,
    }, headers=headers)
    user_id = r.json()["id"]
    r = api_client.post("/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers)
    user_key = {"X-API-Key": f"{r.json()['key_id']}.{r.json()['secret']}", "key_id": r.json()["key_id"]}
    env = {
        "suffix": suffix, "role_id": role_id, "ds_id": ds_id, "ds_name": ds_name,
        "user_id": user_id, "user_key": user_key, "admin_headers": headers,
        "admin_username": admin_username, "user_name": f"u_{suffix}",
    }
    yield env
    # 清理
    with pg_session_factory() as s:
        s.query(AuditLog).filter_by(user_id=user_id).delete()
        s.query(ApiKey).filter_by(user_id=user_id).delete()
        s.query(DatasetGrant).filter_by(role_id=role_id).delete()
        s.query(User).filter_by(id=user_id).delete()
        s.query(Role).filter_by(id=role_id).delete()
        s.query(Dataset).filter_by(id=ds_id).delete()
        s.commit()
    _cleanup_user(pg_session_factory, admin_username)


def test_list_and_query_scoped(api_client, scoped_env):
    env = scoped_env
    r = api_client.get("/api/v1/datasets", headers=env["user_key"])
    assert r.status_code == 200
    assert env["ds_name"] in [d["name"] for d in r.json()]

    r = api_client.get(f"/api/v1/datasets/{env['ds_name']}/rows", headers=env["user_key"])
    body = r.json()
    assert body["total"] == 1
    assert body["rows"][0]["region"] == "华东"
    assert "cost" not in body["rows"][0]
    assert body["columns"] == ["id", "region", "amount"]

    r = api_client.get("/api/v1/admin/audit-logs?limit=10", headers=env["admin_headers"])
    assert any(
        log["dataset"] == env["ds_name"] and log["action"] == "query" for log in r.json()
    )


def test_query_no_grant_403(api_client, pg_session_factory, scoped_env):
    env = scoped_env
    # 新建一个无授权角色的用户
    headers = env["admin_headers"]
    suffix = uuid.uuid4().hex[:8]
    r = api_client.post("/api/v1/admin/roles", json={"name": f"none_{suffix}"}, headers=headers)
    role_id = r.json()["id"]
    r = api_client.post("/api/v1/admin/users", json={
        "username": f"n_{suffix}", "password": "pw123456", "role_id": role_id,
    }, headers=headers)
    user_id = r.json()["id"]
    r = api_client.post("/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers)
    key = {"X-API-Key": f"{r.json()['key_id']}.{r.json()['secret']}"}
    try:
        r = api_client.get(f"/api/v1/datasets/{env['ds_name']}/rows", headers=key)
        assert r.status_code == 403
    finally:
        with pg_session_factory() as s:
            s.query(ApiKey).filter_by(user_id=user_id).delete()
            s.query(User).filter_by(id=user_id).delete()
            s.query(Role).filter_by(id=role_id).delete()
            s.commit()


def test_query_bad_filter_400(api_client, scoped_env):
    r = api_client.get(
        f"/api/v1/datasets/{scoped_env['ds_name']}/rows",
        params={"hacker": "1"}, headers=scoped_env["user_key"],
    )
    assert r.status_code == 400


def test_query_limit_cap_422(api_client, scoped_env):
    r = api_client.get(
        f"/api/v1/datasets/{scoped_env['ds_name']}/rows",
        params={"limit": "501"}, headers=scoped_env["user_key"],
    )
    assert r.status_code == 422  # FastAPI 原生参数校验

