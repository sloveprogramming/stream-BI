"""用 OpenAPI schema 确认真实可用的路径（比遍历 app.routes 可靠）。"""
from app.main import create_app

app = create_app()
schema = app.openapi()
paths = sorted(schema.get("paths", {}).keys())

WANT = ("tenants", "grant-requests", "quality", "metrics")
for p in paths:
    if any(k in p for k in WANT):
        methods = ",".join(sorted(m.upper() for m in schema["paths"][p]))
        print(f"  {methods:20s} {p}")

print("---")
print("governance paths:", len([p for p in paths if any(k in p for k in WANT)]))
print("total paths:", len(paths))
