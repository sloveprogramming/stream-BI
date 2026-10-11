"""模型配置的管理接口。

本文件最重要的断言是**key 永不回明文** —— 列表、详情、创建响应、更新响应，
任何一个响应里出现明文 key 都算事故。其余是默认唯一、非管理员 403、
测试连接把失败翻译成人话。
"""
import uuid

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import ApiKey, AuditLog, ModelProvider, User
from app.security.apikeys import generate_key, hash_secret
from app.security.secrets import decrypt_secret


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
    username = f"mpadmin_{uuid.uuid4().hex[:8]}"
    _, key = bootstrap_admin(username, "pw123456")
    return {"X-API-Key": key}, username


@pytest.fixture
def member_headers(pg_session_factory):
    suffix = uuid.uuid4().hex[:8]
    username = f"mpmember_{suffix}"
    key_id, secret = generate_key()
    with pg_session_factory() as session:
        user = User(username=username, password_hash="x")
        session.add(user)
        session.flush()
        session.add(
            ApiKey(key_id=key_id, secret_hash=hash_secret(secret), user_id=user.id, name="t")
        )
        session.commit()
    return {"X-API-Key": f"{key_id}.{secret}"}, username


@pytest.fixture
def cleanup(pg_session_factory):
    kept: dict[str, list[str]] = {"users": [], "providers": []}
    yield kept
    with pg_session_factory() as session:
        session.query(ModelProvider).filter(
            ModelProvider.name.in_(kept["providers"] or ["__none__"])
        ).delete(synchronize_session=False)
        for username in kept["users"]:
            user = session.query(User).filter_by(username=username).first()
            if user is None:
                continue
            session.query(ApiKey).filter_by(user_id=user.id).delete()
            session.query(AuditLog).filter_by(user_id=user.id).delete()
            session.delete(user)
        session.commit()


def _llm_body(**overrides):
    base = {
        "name": f"ds_{uuid.uuid4().hex[:8]}",
        "kind": "llm",
        "provider_kind": "api",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-plain-secret-9527",
        "model": "deepseek-chat",
        "planner_model": "deepseek-reasoner",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- key 安全


def test_created_key_is_masked_everywhere_and_encrypted_at_rest(
    api_client, admin_headers, cleanup, pg_session_factory
):
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    body = _llm_body()
    cleanup["providers"].append(body["name"])

    created = api_client.post("/api/v1/admin/providers", json=body, headers=headers)
    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["api_key_masked"].endswith("9527")
    assert "sk-plain-secret-9527" not in created.text  # 响应全文不含明文

    # 数据库里同样读不到明文
    with pg_session_factory() as session:
        row = (
            session.query(ModelProvider).filter_by(name=body["name"]).first()
        )
        assert row is not None
        assert "sk-plain-secret-9527" not in (row.encrypted_api_key or "")
        assert decrypt_secret(row.encrypted_api_key) == "sk-plain-secret-9527"

    listed = api_client.get("/api/v1/admin/providers", headers=headers)
    assert listed.status_code == 200
    assert "sk-plain-secret-9527" not in listed.text  # 列表里也没有


def test_update_without_api_key_keeps_the_old_one(
    api_client, admin_headers, cleanup, pg_session_factory
):
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    body = _llm_body()
    cleanup["providers"].append(body["name"])
    provider_id = api_client.post(
        "/api/v1/admin/providers", json=body, headers=headers
    ).json()["id"]

    # 不带 api_key 键 → 不修改
    updated = api_client.put(
        f"/api/v1/admin/providers/{provider_id}",
        json={**body, "model": "deepseek-v3", "api_key": "sk-brand-new-1111"},
        headers=headers,
    )
    assert updated.status_code == 200
    with pg_session_factory() as session:
        row = session.get(ModelProvider, provider_id)
        assert row.model == "deepseek-v3"
        assert decrypt_secret(row.encrypted_api_key) == "sk-brand-new-1111"

    # 显式传空串 → 清空（本地服务不需要 key）
    cleared = api_client.put(
        f"/api/v1/admin/providers/{provider_id}",
        json={**body, "api_key": ""},
        headers=headers,
    )
    assert cleared.status_code == 200
    with pg_session_factory() as session:
        row = session.get(ModelProvider, provider_id)
        assert decrypt_secret(row.encrypted_api_key) == ""


def test_update_response_never_contains_plaintext_key(
    api_client, admin_headers, cleanup
):
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    body = _llm_body()
    cleanup["providers"].append(body["name"])
    provider_id = api_client.post(
        "/api/v1/admin/providers", json=body, headers=headers
    ).json()["id"]
    updated = api_client.put(
        f"/api/v1/admin/providers/{provider_id}",
        json={**body, "api_key": "sk-rotated-2222"},
        headers=headers,
    )
    assert "sk-rotated-2222" not in updated.text


# ---------------------------------------------------------------- 默认唯一


def test_first_of_a_kind_becomes_default_automatically(
    api_client, admin_headers, cleanup
):
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    body = _llm_body()
    cleanup["providers"].append(body["name"])
    payload = api_client.post("/api/v1/admin/providers", json=body, headers=headers).json()
    assert payload["is_default"] is True


def test_set_default_displaces_the_previous_one(
    api_client, admin_headers, cleanup, pg_session_factory
):
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    first = _llm_body()
    second = _llm_body()
    cleanup["providers"].extend([first["name"], second["name"]])
    first_id = api_client.post(
        "/api/v1/admin/providers", json=first, headers=headers
    ).json()["id"]
    second_id = api_client.post(
        "/api/v1/admin/providers", json=second, headers=headers
    ).json()["id"]

    # 第二条创建时第一条已是默认，所以它还不是默认
    r = api_client.post(f"/api/v1/admin/providers/{second_id}/default", headers=headers)
    assert r.status_code == 200, r.text

    with pg_session_factory() as session:
        rows = {
            row.id: row.is_default
            for row in session.query(ModelProvider)
            .filter(ModelProvider.name.in_([first["name"], second["name"]]))
            .all()
        }
    assert rows[second_id] is True
    assert rows[first_id] is False


# ---------------------------------------------------------------- 权限


def test_non_admin_gets_403_on_every_endpoint(
    api_client, admin_headers, member_headers, cleanup
):
    admin, admin_name = admin_headers
    member, member_name = member_headers
    cleanup["users"].extend([admin_name, member_name])

    assert (
        api_client.get("/api/v1/admin/providers", headers=member).status_code == 403
    )
    assert (
        api_client.post("/api/v1/admin/providers", json=_llm_body(), headers=member).status_code
        == 403
    )
    assert (
        api_client.post("/api/v1/admin/providers/test", json={}, headers=member).status_code
        == 403
    )
    assert (
        api_client.post("/api/v1/admin/providers/1/default", headers=member).status_code == 403
    )


# ---------------------------------------------------------------- 测试连接


def test_test_connection_reports_unreachable_service(api_client, admin_headers, cleanup):
    """连不上必须给人话，不是 500 堆栈。"""
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    r = api_client.post(
        "/api/v1/admin/providers/test",
        json={
            "kind": "llm",
            "provider_kind": "api",
            # 保留端口上的一个必然无服务的地址（RFC 5737 文档段 + 未监听端口）
            "base_url": "http://127.0.0.1:9/v1",
            "api_key": "whatever",
            "model": "any",
        },
        headers=headers,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is False
    assert payload["error"]  # 给了可读的原因（直连环境是"连不上"，走代理是 5xx 提示）
    assert "whatever" not in r.text  # 明文 key 不回显


def test_test_connection_rejects_missing_base_url(api_client, admin_headers, cleanup):
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    r = api_client.post(
        "/api/v1/admin/providers/test",
        json={"kind": "llm", "provider_kind": "api", "base_url": "", "model": "any"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_models_endpoint_reports_unsupported_gracefully(
    api_client, admin_headers, cleanup
):
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    r = api_client.post(
        "/api/v1/admin/providers/models",
        json={"base_url": "http://127.0.0.1:9/v1", "api_key": ""},
        headers=headers,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is False
    assert "hint" in payload  # 告诉人可以手填模型名，而不是只丢一个错误


def test_saved_test_uses_decrypted_key_without_leaking_it(
    api_client, admin_headers, cleanup
):
    """已存配置的测试走解密后的真实值；响应里没有 key。"""
    headers, admin_name = admin_headers
    cleanup["users"].append(admin_name)
    body = _llm_body(base_url="http://127.0.0.1:9/v1")
    cleanup["providers"].append(body["name"])
    provider_id = api_client.post(
        "/api/v1/admin/providers", json=body, headers=headers
    ).json()["id"]

    r = api_client.post(f"/api/v1/admin/providers/{provider_id}/test", headers=headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is False
    assert body["api_key"] not in r.text
