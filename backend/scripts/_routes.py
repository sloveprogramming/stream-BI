"""确认新增路由都已注册（启动后可达）。输出用 ASCII 避免终端编码问题。"""
from fastapi.routing import APIRoute

from app.main import create_app

app = create_app()
paths = sorted({r.path for r in app.routes if isinstance(r, APIRoute)})

WANT = ("tenants", "grant-requests", "quality", "metrics")
found = [p for p in paths if any(k in p for k in WANT)]
for p in found:
    print(p)
print("---")
print("governance routes:", len(found))
print("total routes:", len(paths))
