import contextvars
import hashlib
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from app.settings import get_settings

# 每个请求的身份（由 MCPAuthMiddleware 写入，工具读取）
PRINCIPAL_CTX: contextvars.ContextVar = contextvars.ContextVar("mcp_principal", default=None)

mcp_app = FastMCP(
    "bisql",
    stateless_http=True,
    json_response=True,
)


@dataclass
class McpPrincipal:
    user_id: int
    username: str
    role_id: int | None
    is_admin: bool
    api_key_id: int | None


class AnyPrincipalLike:
    def __init__(self, user, api_key_id: int | None):
        self.user = user
        self.api_key_id = api_key_id


def _principal() -> McpPrincipal:
    principal = PRINCIPAL_CTX.get()
    if principal is None:
        raise RuntimeError("MCP 通道身份缺失")
    return principal


def _get_principal() -> AnyPrincipalLike:
    class _User:
        pass

    p = _principal()
    u = _User()
    u.id = p.user_id
    u.username = p.username
    u.role_id = p.role_id
    u.is_admin = p.is_admin
    return AnyPrincipalLike(user=u, api_key_id=p.api_key_id)


def authenticate_key(header_value: str) -> McpPrincipal | None:
    """校验 X-API-Key（key_id.secret），返回身份或 None。独立实现避免依赖请求上下文。"""
    from app.db import make_engine, make_session_factory
    from app.models import ApiKey, User

    key_id, _, secret = header_value.partition(".")
    if not key_id or not secret:
        return None
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    try:
        with sf() as session:
            api_key = session.query(ApiKey).filter_by(key_id=key_id).first()
            if api_key is None or api_key.secret_hash != hashlib.sha256(secret.encode()).hexdigest():
                return None
            user = session.get(User, api_key.user_id)
            if not api_key.enabled or user is None or not user.enabled:
                return None
            return McpPrincipal(
                user_id=user.id,
                username=user.username,
                role_id=user.role_id,
                is_admin=user.is_admin,
                api_key_id=api_key.id,
            )
    finally:
        engine.dispose()


@mcp_app.tool()
def list_datasets() -> list[dict]:
    """列出当前身份有权限查询的所有数据集。"""
    from app.mcp_tools import mcp_list_datasets

    return mcp_list_datasets(_get_principal())


@mcp_app.tool()
def describe_dataset(name: str) -> dict:
    """查看数据集的可用字段与行级权限说明。"""
    from app.mcp_tools import mcp_describe_dataset

    return mcp_describe_dataset(_get_principal(), name)


@mcp_app.tool()
def query_dataset(name: str, filters: dict | None = None, limit: int = 50) -> dict:
    """按权限查询数据集。filters 为 {字段: 值} 等值过滤；返回 columns/rows/total。"""
    from app.mcp_tools import mcp_query_dataset

    return mcp_query_dataset(_get_principal(), name, filters, limit)



