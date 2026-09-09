from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


@dataclass
class Context:
    session_factory: sessionmaker
    erp_engine: Engine
    pg_engine: Engine
    settings: object


def get_ctx(request: Request) -> Context:
    return Context(
        session_factory=request.app.state.session_factory,
        erp_engine=request.app.state.erp_engine,
        pg_engine=request.app.state.pg_engine,
        settings=request.app.state.settings,
    )


@dataclass
class Principal:
    user: object
    api_key: object


def get_principal(request: Request, ctx: Context = Depends(get_ctx)) -> Principal:
    from app.access.ratelimit import LimiterHolder, RateLimitExceeded
    from app.models import ApiKey, User
    from app.security.apikeys import hash_secret

    header = request.headers.get("X-API-Key", "")
    key_id, _, secret = header.partition(".")
    if not key_id or not secret:
        raise HTTPException(status_code=401, detail="missing api key")
    with ctx.session_factory() as session:
        api_key = session.query(ApiKey).filter_by(key_id=key_id).first()
        if api_key is None or api_key.secret_hash != hash_secret(secret):
            raise HTTPException(status_code=401, detail="invalid api key")
        user = session.get(User, api_key.user_id)
        if not api_key.enabled or user is None or not user.enabled:
            raise HTTPException(status_code=401, detail="api key disabled")
        try:
            LimiterHolder.get(ctx.settings).check(key_id)
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        return Principal(user=user, api_key=api_key)

@dataclass
class WebPrincipal:
    user: object


def get_web_principal(request: Request, ctx: Context = Depends(get_ctx)) -> WebPrincipal:
    from app.models import User
    from app.security.jwts import decode_token

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing token")
    try:
        payload = decode_token(auth.removeprefix("Bearer "))
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")
    with ctx.session_factory() as session:
        user = session.get(User, int(payload["sub"]))
        if user is None or not user.enabled:
            raise HTTPException(status_code=403, detail="user disabled")
        return WebPrincipal(user=user)
