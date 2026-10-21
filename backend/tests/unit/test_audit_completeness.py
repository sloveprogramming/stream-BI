"""审计补全：授权变更 / 登录失败 / 导出下载。

合规审计最常查的就是这三类事件。断言里有一条贯穿始终的底线：
**审计 detail 里绝不能出现密码或明文密钥**。
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import AuditLog, Dataset, DatasetGrant, User

# 事件名（与实现约定一致）
ACTION_GRANT_CREATE = "grant.create"
ACTION_GRANT_REVOKE = "grant.revoke"
ACTION_LOGIN_FAILED = "login.failed"
ACTION_EXPORT_DOWNLOAD = "export.download"
ACTION_QUALITY_ANOMALY = "quality.anomaly"

# 导出目录必须可写：默认 backend/exports 在本机沙箱里只读
_TEST_EXPORT_DIR = Path(__file__).resolve().parents[3] / ".testwork" / "exports"
os.environ["BISQL_EXPORT_DIR"] = str(_TEST_EXPORT_DIR)

from app.settings import get_settings  # noqa: E402

get_settings.cache_clear()


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000
    export_max_rows = 50000
    export_dir = str(_TEST_EXPORT_DIR)


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
    app.state.pg_engine = pg_engine
    app.state.session_factory = pg_session_factory
    return TestClient(app)


def _mk(api_client, headers, suffix):
    with api_client.app.state.pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.au_t (id integer PRIMARY KEY, v text)"
        ))
        conn.execute(sa.text(
            "INSERT INTO ods.au_t VALUES (1,'a'),(2,'b') ON CONFLICT (id) DO NOTHING"
        ))
    ds = api_client.post("/api/v1/admin/datasets", json={
        "name": f"au_{suffix}", "source_table": "ods.au_t",
        "pk_columns": ["id"], "columns": ["id", "v"],
    }, headers=headers)
    assert ds.status_code == 201, ds.text
    user = api_client.post("/api/v1/admin/users", json={
        "username": f"auu_{suffix}", "password": "pw123456",
    }, headers=headers)
    assert user.status_code == 201, user.text
    return {"ds": ds.json()["id"], "user": user.json()["id"], "suffix": suffix}


def _latest_action(session, action: str):
    """取最近一条指定 action 的审计。

    `detail` 是普通 JSON 列（不是 JSONB），没有 `.astext`，
    所以按 action 取出来后在 Python 侧筛，不写方言相关的 JSON 表达式。
    """
    return (
        session.query(AuditLog)
        .filter_by(action=action)
        .order_by(AuditLog.id.desc())
        .first()
    )


def _purge_audit(session, action: str, key: str, value) -> int:
    """删掉某个 action 下 detail[key] == value 的审计（测试清理用）。"""
    rows = session.query(AuditLog).filter_by(action=action).all()
    n = 0
    for row in rows:
        if (row.detail or {}).get(key) == value:
            session.delete(row)
            n += 1
    return n


def _cleanup(session_factory, created):
    with session_factory() as s:
        _purge_audit(s, ACTION_GRANT_CREATE, "dataset_id", created["ds"])
        _purge_audit(s, ACTION_GRANT_REVOKE, "dataset_id", created["ds"])
        s.query(DatasetGrant).filter_by(dataset_id=created["ds"]).delete(
            synchronize_session=False
        )
        u = s.query(User).filter_by(username=f"auu_{created['suffix']}").first()
        if u is not None:
            s.query(AuditLog).filter_by(user_id=u.id).delete(synchronize_session=False)
            from app.models import ApiKey

            s.query(ApiKey).filter_by(user_id=u.id).delete(synchronize_session=False)
            s.query(User).filter_by(id=u.id).delete(synchronize_session=False)
        s.query(Dataset).filter_by(id=created["ds"]).delete(synchronize_session=False)
        s.commit()


# ---------------------------------------------------------------- 授权变更


def test_grant_create_is_audited(api_client, pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"aua_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/grants", json={
            "user_id": created["user"], "dataset_id": created["ds"],
            "row_rules": [{"field": "v", "op": "eq", "value": "a"}],
            "field_whitelist": ["id", "v"],
            "days": 7,
        }, headers=headers)
        assert r.status_code == 201, r.text
        grant_id = r.json()["id"]

        with pg_session_factory() as s:
            log = (
                s.query(AuditLog)
                .filter_by(action=ACTION_GRANT_CREATE)
                .order_by(AuditLog.id.desc())
                .first()
            )
            assert log is not None, "授权创建没有留下审计"
            d = log.detail or {}
            assert d.get("grant_id") == grant_id
            assert d.get("dataset_id") == created["ds"]
            # 规则快照必须留住：事后要能还原"当时开的是什么权限"
            assert d.get("row_rules") == [{"field": "v", "op": "eq", "value": "a"}]
            assert d.get("field_whitelist") == ["id", "v"]
            assert d.get("expires_at"), "有效期要进审计"
    finally:
        _cleanup(pg_session_factory, created)


def test_grant_revoke_is_audited(api_client, pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"aub_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/grants", json={
            "user_id": created["user"], "dataset_id": created["ds"], "row_rules": [],
        }, headers=headers)
        grant_id = r.json()["id"]
        r = api_client.delete(f"/api/v1/admin/grants/{grant_id}", headers=headers)
        assert r.status_code == 200, r.text

        with pg_session_factory() as s:
            log = (
                s.query(AuditLog)
                .filter_by(action=ACTION_GRANT_REVOKE)
                .order_by(AuditLog.id.desc())
                .first()
            )
            assert log is not None, "撤销授权没有留下审计"
            assert (log.detail or {}).get("grant_id") == grant_id
            # 撤销不删记录：授权行还在，状态是 revoked
            g = s.get(DatasetGrant, grant_id)
            assert g is not None and g.status == "revoked"
    finally:
        _cleanup(pg_session_factory, created)


def test_revoked_grant_still_listed_for_audit(api_client, pg_session_factory):
    """管理界面要能看到"曾经有过什么权限"，所以列表含已撤销的。"""
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"auc_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk(api_client, headers, suffix)
    try:
        r = api_client.post("/api/v1/admin/grants", json={
            "user_id": created["user"], "dataset_id": created["ds"], "row_rules": [],
        }, headers=headers)
        grant_id = r.json()["id"]
        api_client.delete(f"/api/v1/admin/grants/{grant_id}", headers=headers)

        r = api_client.get(f"/api/v1/admin/grants?dataset_id={created['ds']}",
                           headers=headers)
        assert r.status_code == 200, r.text
        rows = {g["id"]: g for g in r.json()}
        assert grant_id in rows, "已撤销的授权仍应列出（审计需要）"
        assert rows[grant_id]["status"] == "revoked"
    finally:
        _cleanup(pg_session_factory, created)


# ---------------------------------------------------------------- 登录失败


def test_login_failure_is_audited(api_client, pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"aud_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    api_client.post("/api/v1/admin/users", json={
        "username": f"auu_{suffix}", "password": "pw123456",
    }, headers=headers)

    # 密码错
    r = api_client.post("/api/v1/auth/login", json={
        "username": f"auu_{suffix}", "password": "wrong-password",
    })
    assert r.status_code == 401

    with pg_session_factory() as s:
        log = (
            s.query(AuditLog)
            .filter_by(action=ACTION_LOGIN_FAILED)
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert log is not None, "登录失败没有留下审计"
        d = log.detail or {}
        assert d.get("username") == f"auu_{suffix}"
        assert d.get("reason") == "bad_credentials"
        # ★ 底线：绝不能把密码写进日志
        assert "wrong-password" not in str(d)
        assert "password" not in {k.lower() for k in d}

    # 清理
    with pg_session_factory() as s:
        _purge_audit(s, ACTION_LOGIN_FAILED, "username", f"auu_{suffix}")
        u = s.query(User).filter_by(username=f"auu_{suffix}").first()
        if u is not None:
            from app.models import ApiKey

            s.query(ApiKey).filter_by(user_id=u.id).delete(synchronize_session=False)
            s.query(User).filter_by(id=u.id).delete(synchronize_session=False)
        s.commit()


def test_login_unknown_user_is_audited(api_client, pg_session_factory):
    """用户不存在也要记 —— 这能看出有人在猜用户名。"""
    name = f"ghost_{uuid.uuid4().hex[:8]}"
    r = api_client.post("/api/v1/auth/login", json={
        "username": name, "password": "whatever",
    })
    assert r.status_code == 401
    with pg_session_factory() as s:
        log = (
            s.query(AuditLog)
            .filter_by(action=ACTION_LOGIN_FAILED)
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert log is not None
        assert (log.detail or {}).get("username") == name
        # user_id 留空：失败时可能根本没有这个用户
        assert log.user_id is None
        s.query(AuditLog).filter_by(id=log.id).delete(synchronize_session=False)
        s.commit()


def test_disabled_account_login_is_audited(api_client, pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"aue_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    r = api_client.post("/api/v1/admin/users", json={
        "username": f"dis_{suffix}", "password": "pw123456",
    }, headers=headers)
    uid = r.json()["id"]
    with pg_session_factory() as s:
        u = s.get(User, uid)
        u.enabled = False
        s.commit()

    try:
        r = api_client.post("/api/v1/auth/login", json={
            "username": f"dis_{suffix}", "password": "pw123456",
        })
        assert r.status_code == 403
        with pg_session_factory() as s:
            log = (
                s.query(AuditLog)
                .filter_by(action=ACTION_LOGIN_FAILED)
                .order_by(AuditLog.id.desc())
                .first()
            )
            assert (log.detail or {}).get("reason") == "account_disabled"
    finally:
        with pg_session_factory() as s:
            _purge_audit(s, ACTION_LOGIN_FAILED, "username", f"dis_{suffix}")
            from app.models import ApiKey

            s.query(ApiKey).filter_by(user_id=uid).delete(synchronize_session=False)
            s.query(User).filter_by(id=uid).delete(synchronize_session=False)
            s.commit()


# ---------------------------------------------------------------- 导出下载


def test_export_download_is_audited(api_client, pg_session_factory, tmp_path_factory):
    """报表下载要记审计：谁、什么时候、下了哪个文件、多少行。"""
    from app.reports.service import exports_dir

    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"auf_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    created = _mk(api_client, headers, suffix)
    try:
        # 造一条报表运行记录 + 落一个真文件
        with pg_session_factory() as s:
            from app.models import ReportRun, ReportTask

            task = ReportTask(name=f"aur_{suffix}", dataset_id=created["ds"],
                              format="csv", owner_user_id=created["user"])
            s.add(task)
            s.flush()
            target = exports_dir()
            target.mkdir(parents=True, exist_ok=True)
            fname = f"au_{suffix}.csv"
            (target / fname).write_text("id,v\n1,a\n", encoding="utf-8")
            run = ReportRun(task_id=task.id, file_name=fname, row_count=1,
                            status="success")
            s.add(run)
            s.commit()
            run_id, task_id = run.id, task.id

        # 以 owner 身份登录下载
        r = api_client.post("/api/v1/auth/login", json={
            "username": f"auu_{suffix}", "password": "pw123456",
        })
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        r = api_client.get(f"/api/v1/my/reports/{run_id}/download",
                           headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text

        with pg_session_factory() as s:
            log = (
                s.query(AuditLog)
                .filter_by(action=ACTION_EXPORT_DOWNLOAD)
                .order_by(AuditLog.id.desc())
                .first()
            )
            assert log is not None, "导出下载没有留下审计"
            assert log.channel == "export"
            d = log.detail or {}
            assert d.get("file_name") == fname
            assert d.get("run_id") == run_id
            assert log.row_count == 1
            # 清理这份报表记录
            s.query(ReportRun).filter_by(id=run_id).delete(synchronize_session=False)
            s.query(ReportTask).filter_by(id=task_id).delete(synchronize_session=False)
            s.query(AuditLog).filter_by(id=log.id).delete(synchronize_session=False)
            s.commit()
    finally:
        _cleanup(pg_session_factory, created)


def test_audit_actions_are_filterable(api_client, pg_session_factory):
    """管理接口要能按 action 拉取审计（前端做筛选）。"""
    suffix = uuid.uuid4().hex[:8]
    _, key = bootstrap_admin(f"aug_{suffix}", "pw123456")
    headers = {"X-API-Key": key}
    r = api_client.get("/api/v1/admin/audit-logs?limit=5", headers=headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
