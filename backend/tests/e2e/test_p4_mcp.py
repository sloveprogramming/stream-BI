import asyncio
import json
import socket
import threading
import time
import uuid

import httpx
import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.bootstrap import bootstrap_admin
from app.main import create_app
from app.models import ApiKey, AuditLog, Dataset, DatasetGrant, Role, User


class FakeSettings:
    rate_limit_per_minute = 1000
    rate_limit_per_day = 100000
    export_max_rows = 50000


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_server_env(pg_session_factory, pg_engine):
    from app.access.ratelimit import LimiterHolder

    LimiterHolder._limiter = None  # 防止其他用例遗留的限流配置影响本模块
    suffix = uuid.uuid4().hex[:8]
    app = create_app()

    def override_ctx(request: Request):
        return Context(
            session_factory=pg_session_factory,
            erp_engine=object(),
            pg_engine=pg_engine,
            settings=FakeSettings(),
        )

    app.dependency_overrides[get_ctx] = override_ctx

    _, admin_key = bootstrap_admin(f"m4adm_{suffix}", "pw123456")
    headers = {"X-API-Key": admin_key}
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS ods.m4_t "
            "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
        ))
        conn.execute(sa.text(
            "INSERT INTO ods.m4_t VALUES (1,'华东',10,5),(2,'华北',20,8),(3,'华东',30,9) "
            "ON CONFLICT (id) DO NOTHING"
        ))
    asgi = TestClient(app)
    r = asgi.post("/api/v1/admin/roles", json={"name": f"r_{suffix}"}, headers=headers)
    role_id = r.json()["id"]
    r = asgi.post("/api/v1/admin/datasets", json={
        "name": f"m4d_{suffix}", "source_table": "ods.m4_t",
        "pk_columns": ["id"], "columns": ["id", "region", "amount", "cost"],
    }, headers=headers)
    assert r.status_code == 201, r.text
    ds_id, ds_name = r.json()["id"], r.json()["name"]
    r = asgi.post("/api/v1/admin/grants", json={
        "role_id": role_id, "dataset_id": ds_id,
        "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
        "field_whitelist": ["id", "region", "amount"],
    }, headers=headers)
    assert r.status_code == 201, r.text
    r = asgi.post("/api/v1/admin/users", json={
        "username": f"u_{suffix}", "password": "pw123456", "role_id": role_id,
    }, headers=headers)
    user_id = r.json()["id"]
    r = asgi.post("/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers)
    kb = r.json()
    user_key = f"{kb['key_id']}.{kb['secret']}"

    port = _free_port()
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1)
            break
        except Exception:
            time.sleep(0.3)

    yield {"port": port, "user_key": user_key, "ds_name": ds_name, "user_id": user_id,
           "role_id": role_id, "ds_id": ds_id, "pg_session_factory": pg_session_factory}

    server.should_exit = True
    thread.join(timeout=10)
    with pg_session_factory() as s:
        s.query(AuditLog).filter_by(user_id=user_id).delete()
        s.query(ApiKey).filter_by(user_id=user_id).delete()
        s.query(User).filter_by(id=user_id).delete()
        s.query(DatasetGrant).filter_by(role_id=role_id).delete()
        s.query(Role).filter_by(id=role_id).delete()
        s.query(Dataset).filter_by(id=ds_id).delete()
        adm = s.query(User).filter_by(username=f"m4adm_{suffix}").first()
        if adm is not None:
            s.query(ApiKey).filter_by(user_id=adm.id).delete()
            s.delete(adm)
        s.commit()


def test_mcp_http_401_without_key(mcp_server_env):
    r = httpx.post(f"http://127.0.0.1:{mcp_server_env['port']}/mcp", timeout=10)
    assert r.status_code == 401


def test_mcp_client_end_to_end(mcp_server_env):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    env = mcp_server_env
    url = f"http://127.0.0.1:{env['port']}/mcp"

    async def scenario():
        async with streamablehttp_client(url, headers={"X-API-Key": env["user_key"]}) as (
            read, write, get_session_id
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool(
                    "query_dataset", {"name": env["ds_name"]}
                )
                return tools, result

    tools, result = asyncio.run(scenario())
    names = {t.name for t in tools.tools}
    assert {"list_datasets", "describe_dataset", "query_dataset"} <= names
    data = getattr(result, "data", None) or json.loads(result.content[0].text)
    assert data["total"] == 2
    assert data["rows"][0]["region"] == "华东"
    assert "cost" not in data["columns"]

