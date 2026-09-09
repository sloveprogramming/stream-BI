from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import Context, WebPrincipal, get_ctx, get_web_principal
from app.models import User
from app.security.jwts import create_token
from app.security.passwords import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody, ctx: Context = Depends(get_ctx)):
    with ctx.session_factory() as session:
        user = session.query(User).filter_by(username=body.username).first()
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not user.enabled:
            raise HTTPException(status_code=403, detail="账号已停用")
        token = create_token(user.id, user.username, user.is_admin)
        return {
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "is_admin": user.is_admin,
            },
        }


@router.get("/me")
def me(principal: WebPrincipal = Depends(get_web_principal)):
    u = principal.user
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "is_admin": u.is_admin,
    }
