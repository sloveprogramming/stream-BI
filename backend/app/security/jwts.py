from datetime import datetime, timedelta, timezone

import jwt

from app.settings import get_settings


def create_token(user_id: int, username: str, is_admin: bool) -> str:
    s = get_settings()
    payload = {
        "sub": str(user_id),
        "username": username,
        "is_admin": is_admin,
        "exp": datetime.now(timezone.utc) + timedelta(hours=s.jwt_expire_hours),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
