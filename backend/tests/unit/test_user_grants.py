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
    export_max_rows = 50000


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


def test_user_direct_grant_lifecycle(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    _, admin_key = bootstrap_admin(f"gadm_{suffix}", "pw123456")
    admin_headers = {"X-API-Key": admin_key}
    created = []
    try:
        with pg_engine.begin() as conn:
            conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
            conn.execute(sa.text(
                "CREATE TABLE IF NOT EXISTS ods.direct_t "
                "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
            ))
            conn.execute(sa.text(
                "INSERT INTO ods.direct_t VALUES (1,'华东',10,5),(2,'华北',20,8) "
                "ON CONFLICT (id) DO NOTHING"
            ))
        r = api_client.post("/api/v1/admin/datasets", headers=admin_headers, json={
            "name": f"direct_{suffix}", "source_table": "ods.direct_t",
            "pk_columns": ["id"], "columns": ["id", "region", "amount", "cost"],
        })
        ds_id = r.json()["id"]
        r = api_client.post("/api/v1/admin/users", headers=admin_headers, json={
            "username": f"du_{suffix}", "password": "pw123456",
        })
        user_id = r.json()["id"]
        # 用户直授（不经过角色）
        r = api_client.post("/api/v1/admin/grants", headers=admin_headers, json={
            "user_id": user_id, "dataset_id": ds_id,
            "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
            "field_whitelist": ["id", "region", "amount"],
        })
        assert r.status_code == 201, r.text
        assert r.json()["target"].startswith("user:")
        # 按数据集列出授权
        r = api_client.get(f"/api/v1/admin/grants?dataset_id={ds_id}", headers=admin_headers)
        assert r.status_code == 200
        assert any(g["target"].startswith("user:") for g in r.json())
        grant_id = r.json()[0]["id"]
        # 用户自己的 Key 查询：行级裁剪生效
        r = api_client.post("/api/v1/admin/api-keys", headers=admin_headers, json={"user_id": user_id})
        kb = r.json()
        user_headers = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}
        r = api_client.get(f"/api/v1/datasets/direct_{suffix}/rows", headers=user_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["rows"][0]["region"] == "华东"
        assert "cost" not in body["rows"][0]
        # 删除授权后 403
        r = api_client.delete(f"/api/v1/admin/grants/{grant_id}", headers=admin_headers)
        assert r.status_code == 200
        r = api_client.get(f"/api/v1/datasets/direct_{suffix}/rows", headers=user_headers)
        assert r.status_code == 403
    finally:
        with pg_session_factory() as s:
            ds = s.query(Dataset).filter_by(name=f"direct_{suffix}").first()
            if ds is not None:
                s.query(DatasetGrant).filter_by(dataset_id=ds.id).delete()
                s.query(Dataset).filter_by(id=ds.id).delete()
            u = s.query(User).filter_by(username=f"du_{suffix}").first()
            if u is not None:
                s.query(AuditLog).filter_by(user_id=u.id).delete()
                s.query(ApiKey).filter_by(user_id=u.id).delete()
                s.delete(u)
            adm = s.query(User).filter_by(username=f"gadm_{suffix}").first()
            if adm is not None:
                # 授权变更会写审计（action=grant.create/revoke），
                # 这些日志的 user_id 指向管理员本人，必须先清才能删用户
                s.query(AuditLog).filter(
                    AuditLog.user_id == adm.id,
                    AuditLog.action.in_(["grant.create", "grant.revoke", "grant.approve"]),
                ).delete(synchronize_session=False)
                s.query(ApiKey).filter_by(user_id=adm.id).delete()
                s.delete(adm)
            s.commit()
