import json

from starlette.responses import JSONResponse


class MCPAuthMiddleware:
    """对 /mcp 路径做 API Key 鉴权，并把身份写入 contextvar 供工具读取。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            from app.mcp_server import PRINCIPAL_CTX, authenticate_key

            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            principal = authenticate_key(headers.get("x-api-key", ""))
            if principal is None:
                resp = JSONResponse({"detail": "unauthorized"}, status_code=401)
                await resp(scope, receive, send)
                return
            token = PRINCIPAL_CTX.set(principal)
            try:
                await self.app(scope, receive, send)
            finally:
                PRINCIPAL_CTX.reset(token)
            return
        await self.app(scope, receive, send)
