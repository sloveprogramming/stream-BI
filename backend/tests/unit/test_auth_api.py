import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context
from app.main import create_app
from app.models import User


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000


@pytest.fixture
def web_client(pg_session_factory, pg_engine):
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
    return TestClient(app)


def _make_user(pg_session_factory, username, password_hash, enabled=True):
    with pg_session_factory() as s:
        old = s.query(User).filter_by(username=username).first()
        if old is not None:
            s.delete(old)
            s.commit()
        u = User(username=username, password_hash=password_hash, enabled=enabled)
        s.add(u)
        s.commit()
        return u.id


def test_login_success_and_me(web_client, pg_session_factory):
    from app.security.passwords import hash_password

    uid = _make_user(pg_session_factory, "webuser", hash_password("pw123456"))
    r = web_client.post("/api/v1/auth/login", json={"username": "webuser", "password": "pw123456"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["username"] == "webuser"
    token = body["token"]
    r = web_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["id"] == uid
    with pg_session_factory() as s:
        s.query(User).filter_by(username="webuser").delete()
        s.commit()


def test_login_wrong_password_401(web_client, pg_session_factory):
    from app.security.passwords import hash_password

    _make_user(pg_session_factory, "webuser2", hash_password("pw123456"))
    r = web_client.post("/api/v1/auth/login", json={"username": "webuser2", "password": "wrong"})
    assert r.status_code == 401
    with pg_session_factory() as s:
        s.query(User).filter_by(username="webuser2").delete()
        s.commit()


def test_me_without_token_401(web_client):
    assert web_client.get("/api/v1/auth/me").status_code == 401

