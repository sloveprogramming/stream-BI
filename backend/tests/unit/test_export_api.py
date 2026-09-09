import uuid

import sqlalchemy as sa

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000
    export_max_rows = 50000


def _cleanup_user(pg_session_factory, username):
    with pg_session_factory() as s:
        u = s.query(User).filter_by(username=username).first()
        if u is not None:
            s.query(AuditLog).filter_by(user_id=u.id).delete()
            s.query(ApiKey).filter_by(user_id=u.id).delete()
            s.delete(u)
            s.commit()


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

    from app.api.deps import get_ctx as _g

    app.dependency_overrides[get_ctx] = override_ctx
    return TestClient(app)


def test_export_csv_scoped(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    username = f"exp_{suffix}"
    _, admin_key = bootstrap_admin(f"expadm_{suffix}", "pw123456")
    headers = {"X-API-Key": admin_key}
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.export_t "
            "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
        ))
        conn.execute(sa.text(
            "INSERT INTO ods.export_t VALUES (1,'华东',10,5),(2,'华北',20,8),(3,'华东',30,9) "
            "ON CONFLICT (id) DO NOTHING"
        ))
    try:
        r = api_client.post("/api/v1/admin/roles", json={"name": f"r_{suffix}"}, headers=headers)
        role_id = r.json()["id"]
        r = api_client.post("/api/v1/admin/datasets", json={
            "name": f"expd_{suffix}", "source_table": "ods.export_t",
            "pk_columns": ["id"], "columns": ["id", "region", "amount", "cost"],
        }, headers=headers)
        ds_id, ds_name = r.json()["id"], r.json()["name"]
        api_client.post("/api/v1/admin/grants", json={
            "role_id": role_id, "dataset_id": ds_id,
            "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
            "field_whitelist": ["id", "region", "amount"],
        }, headers=headers)
        r = api_client.post("/api/v1/admin/users", json={
            "username": username, "password": "pw123456", "role_id": role_id,
        }, headers=headers)
        user_id = r.json()["id"]
        r = api_client.post("/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers)
        user_headers = {"X-API-Key": f"{r.json()['key_id']}.{r.json()['secret']}"}

        r = api_client.get(f"/api/v1/datasets/{ds_name}/export?format=csv", headers=user_headers)
        assert r.status_code == 200, r.text
        assert "text/csv" in r.headers["content-type"]
        lines = r.text.strip().splitlines()
        assert lines[0].lstrip(chr(65279)) == "id,region,amount"
        assert len(lines) == 3  # 表头 + 2 行华东
        assert "5" not in lines[1].split(",")  # cost 列不存在

        # xlsx 导出
        r = api_client.get(f"/api/v1/datasets/{ds_name}/export?format=xlsx", headers=user_headers)
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers["content-type"]
    finally:
        with pg_session_factory() as s:
            s.query(AuditLog).filter_by(user_id=user_id).delete()
            s.query(ApiKey).filter_by(user_id=user_id).delete()
            s.query(DatasetGrant).filter_by(role_id=role_id).delete()
            s.query(User).filter_by(id=user_id).delete()
            s.query(Role).filter_by(id=role_id).delete()
            s.query(Dataset).filter_by(id=ds_id).delete()
            s.commit()
        _cleanup_user(pg_session_factory, f"expadm_{suffix}")


def test_export_no_grant_403(api_client, pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    _, admin_key = bootstrap_admin(f"expn_{suffix}", "pw123456")
    headers = {"X-API-Key": admin_key}
    r = api_client.post("/api/v1/admin/users", json={
        "username": f"nx_{suffix}", "password": "pw123456",
    }, headers=headers)
    user_id = r.json()["id"]
    r = api_client.post("/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers)
    key = {"X-API-Key": f"{r.json()['key_id']}.{r.json()['secret']}"}
    r = api_client.get("/api/v1/datasets/nothing/export?format=csv", headers=key)
    assert r.status_code == 404
    with pg_session_factory() as s:
        s.query(ApiKey).filter_by(user_id=user_id).delete()
        s.query(User).filter_by(id=user_id).delete()
        s.commit()





