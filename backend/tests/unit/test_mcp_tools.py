import uuid

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import AnyPrincipal, Context
from app.bootstrap import bootstrap_admin
from app.mcp_tools import mcp_describe_dataset, mcp_list_datasets, mcp_query_dataset
from app.main import create_app
from app.models import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000
    export_max_rows = 50000


@pytest.fixture
def mcp_env(pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    app = create_app()

    def override_ctx(request: Request):
        return Context(
            session_factory=pg_session_factory,
            erp_engine=object(),
            pg_engine=pg_engine,
            settings=FakeSettings(),
        )

    from app.api.deps import get_ctx

    app.dependency_overrides[get_ctx] = override_ctx
    client = TestClient(app)

    _, admin_key = bootstrap_admin(f"mcpadm_{suffix}", "pw123456")
    headers = {"X-API-Key": admin_key}
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.mcp_t "
            "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
        ))
        conn.execute(sa.text(
            "INSERT INTO ods.mcp_t VALUES (1,'华东',10,5),(2,'华北',20,8),(3,'华东',30,9) "
            "ON CONFLICT (id) DO NOTHING"
        ))
    r = client.post("/api/v1/admin/roles", json={"name": f"r_{suffix}"}, headers=headers)
    role_id = r.json()["id"]
    r = client.post("/api/v1/admin/datasets", json={
        "name": f"mcpd_{suffix}", "source_table": "ods.mcp_t",
        "pk_columns": ["id"], "columns": ["id", "region", "amount", "cost"],
    }, headers=headers)
    ds_id, ds_name = r.json()["id"], r.json()["name"]
    client.post("/api/v1/admin/grants", json={
        "role_id": role_id, "dataset_id": ds_id,
        "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
        "field_whitelist": ["id", "region", "amount"],
    }, headers=headers)
    r = client.post("/api/v1/admin/users", json={
        "username": f"u_{suffix}", "password": "pw123456", "role_id": role_id,
    }, headers=headers)
    user_id = r.json()["id"]
    r = client.post("/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers)
    kb = r.json()
    # principal：绕过 HTTP，直接构造（工具核心逻辑测试）
    with pg_session_factory() as s:
        u = s.get(User, user_id)
        principal = AnyPrincipal(user=u)

    def cleanup():
        with pg_session_factory() as s:
            s.query(AuditLog).filter_by(user_id=user_id).delete()
            s.query(ApiKey).filter_by(user_id=user_id).delete()
            s.query(User).filter_by(id=user_id).delete()
            s.query(DatasetGrant).filter_by(role_id=role_id).delete()
            s.query(Role).filter_by(id=role_id).delete()
            s.query(Dataset).filter_by(id=ds_id).delete()
            adm = s.query(User).filter_by(username=f"mcpadm_{suffix}").first()
            if adm is not None:
                s.query(ApiKey).filter_by(user_id=adm.id).delete()
                s.delete(adm)
            s.commit()

    return {
        "principal": principal, "ds_id": ds_id, "ds_name": ds_name,
        "cleanup": cleanup,
    }


def test_list_and_describe(mcp_env):
    env = mcp_env
    ds_list = mcp_list_datasets(env["principal"])
    assert any(d["name"] == env["ds_name"] for d in ds_list)
    desc = mcp_describe_dataset(env["principal"], env["ds_name"])
    assert desc["name"] == env["ds_name"]
    assert set(desc["columns"]) == {"id", "region", "amount"}
    assert "region" in str(desc["row_rules"])


def test_query_dataset_scoped_and_audited(mcp_env, pg_session_factory):
    env = mcp_env
    result = mcp_query_dataset(env["principal"], env["ds_name"], None, 50)
    assert result["total"] == 2  # 华东两行
    assert "cost" not in result["columns"]
    with pg_session_factory() as s:
        logs = (
            s.query(AuditLog)
            .filter_by(channel="mcp", dataset=env["ds_name"])
            .all()
        )
        assert len(logs) >= 1


def test_query_no_grant_raises(mcp_env):
    class NoRole:
        id = None
        is_admin = False

    with pytest.raises(ValueError):
        mcp_query_dataset(AnyPrincipal(user=NoRole()), mcp_env["ds_name"], None, 50)



