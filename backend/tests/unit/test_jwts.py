import pytest

from app.security.jwts import create_token, decode_token


def test_token_roundtrip():
    token = create_token(7, "alice", is_admin=True)
    payload = decode_token(token)
    assert payload["sub"] == "7"
    assert payload["username"] == "alice"
    assert payload["is_admin"] is True


def test_invalid_token_rejected():
    import jwt

    with pytest.raises(jwt.InvalidTokenError):
        decode_token("not-a-token")


def test_expired_token_rejected():
    from datetime import datetime, timedelta, timezone

    import jwt

    from app.settings import get_settings

    token = jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token)
