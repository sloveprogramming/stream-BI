"""删除用户接口回归测试：清理关联数据、保留审计链、自我保护。"""
import uuid

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import (
    ApiKey,
    AuditLog,
    Dataset,
    DatasetGrant,
    ReportRun,
    ReportTask,
    Role,
    User,
)


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


def test_delete_user_cleans_up_and_keeps_audit(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    admin_username = f"del_admin_{suffix}"
    username = f"del_user_{suffix}"
    _, admin_key = bootstrap_admin(admin_username, "pw123456")
    headers = {"X-API-Key": admin_key}

    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS ods.del_probe "
                "(id integer PRIMARY KEY, region text, amount numeric)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO ods.del_probe VALUES (1,'华东',10),(2,'华北',20) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )

    role_id = ds_id = user_id = None
    log_id = None
    try:
        # 角色 + 数据集 + 角色授权（让该用户能查询并产生审计日志、能建报表）
        role_id = api_client.post(
            "/api/v1/admin/roles", json={"name": f"del_r_{suffix}"}, headers=headers
        ).json()["id"]
        ds_id = api_client.post(
            "/api/v1/admin/datasets",
            json={
                "name": f"del_ds_{suffix}",
                "source_table": "ods.del_probe",
                "pk_columns": ["id"],
                "columns": ["id", "region", "amount"],
            },
            headers=headers,
        ).json()["id"]
        assert (
            api_client.post(
                "/api/v1/admin/grants",
                json={"role_id": role_id, "dataset_id": ds_id, "row_rules": []},
                headers=headers,
            ).status_code
            == 201
        )

        user_id = api_client.post(
            "/api/v1/admin/users",
            json={"username": username, "password": "pw123456", "role_id": role_id},
            headers=headers,
        ).json()["id"]
        key = api_client.post(
            "/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers
        ).json()
        user_headers = {"X-API-Key": f"{key['key_id']}.{key['secret']}"}

        # 产生一条归属该用户的审计日志
        r = api_client.get(f"/api/v1/datasets/del_ds_{suffix}/rows", headers=user_headers)
        assert r.status_code == 200, r.text
        with pg_session_factory() as s:
            log_id = (
                s.query(AuditLog.id)
                .filter_by(user_id=user_id, action="query")
                .order_by(AuditLog.id.desc())
                .first()[0]
            )
        # 产生一个归属该用户的数据集直授
        assert (
            api_client.post(
                "/api/v1/admin/grants",
                json={"user_id": user_id, "dataset_id": ds_id, "row_rules": []},
                headers=headers,
            ).status_code
            == 201
        )
        # 产生一个归属该用户的报表任务
        task_id = api_client.post(
            "/api/v1/my/report-tasks",
            json={"name": f"del_task_{suffix}", "dataset_id": ds_id},
            headers=user_headers,
        ).json()["id"]

        # 执行删除
        r = api_client.delete(f"/api/v1/admin/users/{user_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["username"] == username
        assert body["removed"]["api_keys"] == 1
        assert body["removed"]["report_tasks"] == 1

        with pg_session_factory() as s:
            assert s.get(User, user_id) is None
            assert s.query(ApiKey).filter_by(user_id=user_id).count() == 0
            assert (
                s.query(DatasetGrant).filter_by(user_id=user_id).count() == 0
            )
            assert s.get(ReportTask, task_id) is None
            assert s.query(ReportRun).filter_by(task_id=task_id).count() == 0
            # 角色授权不受影响
            assert s.query(DatasetGrant).filter_by(role_id=role_id).count() == 1
            # 审计日志保留，且已解除外键、记录被删用户名
            log = s.get(AuditLog, log_id)
            assert log is not None, "审计日志应保留"
            assert log.user_id is None and log.api_key_id is None
            assert (log.detail or {}).get("deleted_user") == username
    finally:
        with pg_session_factory() as s:
            s.query(ApiKey).filter_by(user_id=user_id).delete()
            s.query(DatasetGrant).filter_by(role_id=role_id).delete()
            if log_id is not None:
                s.query(AuditLog).filter_by(id=log_id).delete()
            if user_id is not None:
                s.query(User).filter_by(id=user_id).delete()
            s.query(Role).filter_by(id=role_id).delete()
            s.query(Dataset).filter_by(id=ds_id).delete()
            s.commit()
        with pg_session_factory() as s:
            admin = s.query(User).filter_by(username=admin_username).first()
            if admin is not None:
                s.query(AuditLog).filter_by(user_id=admin.id).delete()
                s.query(ApiKey).filter_by(user_id=admin.id).delete()
                s.delete(admin)
                s.commit()


def test_cannot_delete_self(api_client, pg_session_factory):
    admin_username = f"self_admin_{uuid.uuid4().hex[:8]}"
    admin, key = bootstrap_admin(admin_username, "pw123456")
    headers = {"X-API-Key": key}
    try:
        r = api_client.delete(f"/api/v1/admin/users/{admin.id}", headers=headers)
        assert r.status_code == 400
        assert "当前登录账号" in r.json()["detail"]
    finally:
        with pg_session_factory() as s:
            s.query(AuditLog).filter_by(user_id=admin.id).delete()
            s.query(ApiKey).filter_by(user_id=admin.id).delete()
            s.query(User).filter_by(id=admin.id).delete()
            s.commit()


def test_delete_missing_user_404(api_client, pg_session_factory):
    admin_username = f"miss_admin_{uuid.uuid4().hex[:8]}"
    admin, key = bootstrap_admin(admin_username, "pw123456")
    headers = {"X-API-Key": key}
    try:
        r = api_client.delete("/api/v1/admin/users/99999999", headers=headers)
        assert r.status_code == 404
    finally:
        with pg_session_factory() as s:
            s.query(AuditLog).filter_by(user_id=admin.id).delete()
            s.query(ApiKey).filter_by(user_id=admin.id).delete()
            s.query(User).filter_by(id=admin.id).delete()
            s.commit()
