import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx, get_principal
from app.models import ApiKey, User
from app.security.apikeys import generate_key, hash_secret


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000


@pytest.fixture
def auth_client(pg_session_factory):
    app = FastAPI()

    @app.get("/whoami")
    def whoami(principal=Depends(get_principal)):
        return {"username": principal.user.username, "key_id": principal.api_key.key_id}

    def override_ctx(request: Request):
        return Context(
            session_factory=pg_session_factory,
            erp_engine=object(),
            pg_engine=object(),
            settings=FakeSettings(),
        )

    app.dependency_overrides[get_ctx] = override_ctx
    return TestClient(app)


def _make_user_with_key(pg_session_factory, username="authuser", enabled=True, key_enabled=True):
    # 防御：清理上次运行残留
    with pg_session_factory() as s:
        old = s.query(User).filter_by(username=username).first()
        if old is not None:
            s.query(ApiKey).filter_by(user_id=old.id).delete()
            s.delete(old)
            s.commit()
    key_id, secret = generate_key()
    with pg_session_factory() as s:
        u = User(username=username, password_hash="x", enabled=enabled)
        s.add(u)
        s.flush()
        s.add(
            ApiKey(
                user_id=u.id, key_id=key_id, secret_hash=hash_secret(secret),
                enabled=key_enabled, name="t",
            )
        )
        s.commit()
        uid = u.id
    return f"{key_id}.{secret}", uid, key_id


def _cleanup(pg_session_factory, key_id, uid):
    with pg_session_factory() as s:
        s.query(ApiKey).filter_by(key_id=key_id).delete()
        s.query(User).filter_by(id=uid).delete()
        s.commit()


def test_valid_key_returns_user(auth_client, pg_session_factory):
    header, uid, key_id = _make_user_with_key(pg_session_factory)
    r = auth_client.get("/whoami", headers={"X-API-Key": header})
    assert r.status_code == 200
    assert r.json()["username"] == "authuser"
    _cleanup(pg_session_factory, key_id, uid)


def test_wrong_secret_401(auth_client, pg_session_factory):
    key_id, secret = generate_key()
    header, uid, key_id = _make_user_with_key(pg_session_factory)
    bad = header.split(".")[0] + ".wrongsecret"
    r = auth_client.get("/whoami", headers={"X-API-Key": bad})
    assert r.status_code == 401
    _cleanup(pg_session_factory, key_id, uid)


def test_disabled_key_401(auth_client, pg_session_factory):
    header, uid, key_id = _make_user_with_key(pg_session_factory, key_enabled=False)
    r = auth_client.get("/whoami", headers={"X-API-Key": header})
    assert r.status_code == 401
    _cleanup(pg_session_factory, key_id, uid)


def test_disabled_user_401(auth_client, pg_session_factory):
    header, uid, key_id = _make_user_with_key(pg_session_factory, enabled=False)
    r = auth_client.get("/whoami", headers={"X-API-Key": header})
    assert r.status_code == 401
    _cleanup(pg_session_factory, key_id, uid)


def test_missing_header_401(auth_client):
    assert auth_client.get("/whoami").status_code == 401


