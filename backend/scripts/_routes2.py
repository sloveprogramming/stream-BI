"""诊断：create_app() 到底注册了什么。"""
from fastapi.routing import APIRoute

from app.main import create_app

app = create_app()
print("app.routes count:", len(app.routes))
for r in app.routes:
    print(f"  {type(r).__name__:20s} {getattr(r, 'path', getattr(r, 'path_format', '?'))}")

print()
api_routes = [r for r in app.routes if isinstance(r, APIRoute)]
print("APIRoute count:", len(api_routes))
paths = sorted({r.path for r in api_routes})
want = [p for p in paths if ("tenant" in p or "grant-request" in p or "quality" in p or "metric" in p)]
print("governance:", want)
