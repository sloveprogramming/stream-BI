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
            # 登录失败必须记审计（合规审计最常查的三类事件之一）。
            # **不记密码**，只记用户名与失败原因 —— 记密码就是把明文凭据写进日志。
            _audit_login_failed(session, body.username, "bad_credentials")
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        if not user.enabled:
            _audit_login_failed(session, body.username, "account_disabled")
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


def _audit_login_failed(session, username: str, reason: str) -> None:
    """记一条登录失败审计。失败不影响登录流程本身。"""
    from app.models import AuditLog

    try:
        session.add(
            AuditLog(
                # user_id 留空：失败时可能根本没有这个用户，
                # 而且"用户名存在但密码错"与"用户不存在"要能区分开——
                # 那个信息只能从 detail 看，不能从 user_id 反推
                user_id=None,
                channel="web",
                dataset=None,
                action="login.failed",
                row_count=0,
                detail={"username": username, "reason": reason},
            )
        )
        session.commit()
    except Exception:  # noqa: BLE001 —— 审计失败不能影响登录错误响应
        pass


@router.get("/me")
def me(principal: WebPrincipal = Depends(get_web_principal)):
    u = principal.user
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "is_admin": u.is_admin,
    }
