"""自动发现 + 管理员可见性回归测试。

覆盖：ods 新表被自动注册为数据集、重复扫描幂等、
管理员无需显式授权即可看到并查询、普通用户仍受授权约束。
"""
import uuid

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User
from app.sync.discovery import discover_datasets


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000
    discovery_schema = "ods"


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


def test_discovery_then_admin_visibility(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    schema = f"ods_disc_{suffix}"
    table = f"t_{suffix}"

    with pg_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(
            sa.text(
                f'CREATE TABLE "{schema}"."{table}" '
                "(id integer PRIMARY KEY, region text, _synced_at timestamptz DEFAULT now())"
            )
        )
        conn.execute(
            sa.text(
                f'INSERT INTO "{schema}"."{table}" (id, region) VALUES (1, \'华东\'), (2, \'华北\')'
            )
        )

    created = discover_datasets(pg_session_factory, pg_engine, schema)
    assert [c["name"] for c in created] == [table]
    # 再次扫描应幂等，不重复注册
    assert discover_datasets(pg_session_factory, pg_engine, schema) == []

    with pg_session_factory() as s:
        ds = s.query(Dataset).filter_by(name=table).first()
        assert ds is not None
        assert ds.source_table == f"{schema}.{table}"
        assert ds.pk_columns == ["id"]
        assert "_synced_at" not in ds.columns  # 内部字段不进入数据集

    admin_user, key = bootstrap_admin(f"disc_admin_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    role_id = user_id = None
    try:
        # 管理员：无需授权即可看到并查询
        r = api_client.get("/api/v1/datasets", headers=headers)
        assert r.status_code == 200
        assert table in [d["name"] for d in r.json()]

        r = api_client.get(f"/api/v1/datasets/{table}/rows", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        assert "_synced_at" not in body["columns"]

        # 普通用户：无授权 → 列表不可见 + 查询 403
        r = api_client.post(
            "/api/v1/admin/roles", json={"name": f"disc_role_{suffix}"}, headers=headers
        )
        role_id = r.json()["id"]
        r = api_client.post(
            "/api/v1/admin/users",
            json={"username": f"disc_u_{suffix}", "password": "pw123456", "role_id": role_id},
            headers=headers,
        )
        user_id = r.json()["id"]
        r = api_client.post(
            "/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers
        )
        user_key = {"X-API-Key": f"{r.json()['key_id']}.{r.json()['secret']}"}

        r = api_client.get("/api/v1/datasets", headers=user_key)
        assert table not in [d["name"] for d in r.json()]
        r = api_client.get(f"/api/v1/datasets/{table}/rows", headers=user_key)
        assert r.status_code == 403
    finally:
        with pg_session_factory() as s:
            ids = [d.id for d in s.query(Dataset).filter_by(name=table).all()]
            if ids:
                s.query(DatasetGrant).filter(
                    DatasetGrant.dataset_id.in_(ids)
                ).delete(synchronize_session=False)
                s.query(Dataset).filter(Dataset.id.in_(ids)).delete(
                    synchronize_session=False
                )
            if user_id is not None:
                s.query(AuditLog).filter_by(user_id=user_id).delete()
                s.query(ApiKey).filter_by(user_id=user_id).delete()
                s.query(User).filter_by(id=user_id).delete()
            if role_id is not None:
                s.query(Role).filter_by(id=role_id).delete()
            s.query(AuditLog).filter_by(user_id=admin_user.id).delete()
            s.query(ApiKey).filter_by(user_id=admin_user.id).delete()
            s.query(User).filter_by(id=admin_user.id).delete()
            s.commit()
        with pg_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
