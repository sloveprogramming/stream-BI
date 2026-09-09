import uuid

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import ApiKey, AuditLog, Dataset, Role, User


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


def test_my_keys_and_admin_lists(api_client, pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    _, admin_key = bootstrap_admin(f"cb_{suffix}", "pw123456")
    headers = {"X-API-Key": admin_key}
    try:
        # 管理员列表接口
        r = api_client.get("/api/v1/admin/users", headers=headers)
        assert r.status_code == 200
        r = api_client.post("/api/v1/admin/roles", json={"name": f"r_{suffix}"}, headers=headers)
        role_id = r.json()["id"]
        r = api_client.get("/api/v1/admin/roles", headers=headers)
        assert any(item["id"] == role_id for item in r.json())
        r = api_client.post("/api/v1/admin/datasets", json={
            "name": f"cbd_{suffix}", "source_table": "ods.cbt",
            "pk_columns": ["id"], "columns": ["id"],
        }, headers=headers)
        assert r.status_code == 400  # ods.cbt 不存在 → 表校验拒绝
        r = api_client.get("/api/v1/admin/datasets", headers=headers)
        assert r.status_code == 200
        # 用户 JWT 创建自己的 Key
        api_client.post("/api/v1/admin/users", json={
            "username": f"cbu_{suffix}", "password": "pw123456",
        }, headers=headers)
        r = api_client.post("/api/v1/auth/login", json={
            "username": f"cbu_{suffix}", "password": "pw123456",
        })
        token = r.json()["token"]
        web = {"Authorization": f"Bearer {token}"}
        r = api_client.post("/api/v1/my/keys", json={"name": "mine"}, headers=web)
        assert r.status_code == 201, r.text
        assert r.json()["secret"]
        key_id = r.json()["key_id"]
        r = api_client.get("/api/v1/my/keys", headers=web)
        assert any(k["key_id"] == key_id for k in r.json())
        # 撤销自己的 Key
        my_keys = api_client.get("/api/v1/my/keys", headers=web).json()
        mine = next(k for k in my_keys if k["key_id"] == key_id)
        r = api_client.post(f"/api/v1/my/keys/{mine['id']}/revoke", headers=web)
        assert r.json()["enabled"] is False
    finally:
        with pg_session_factory() as s:
            for name in (f"cbu_{suffix}", f"cb_{suffix}"):
                u = s.query(User).filter_by(username=name).first()
                if u is not None:
                    s.query(AuditLog).filter_by(user_id=u.id).delete()
                    s.query(ApiKey).filter_by(user_id=u.id).delete()
                    s.delete(u)
            rle = s.query(Role).filter_by(name=f"r_{suffix}").first()
            if rle is not None:
                s.delete(rle)
            s.commit()
