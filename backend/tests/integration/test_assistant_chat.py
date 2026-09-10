import json
import uuid

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
    assistant_max_rows = 1000


@pytest.fixture
def api_client(pg_session_factory, pg_engine):
    app = create_app()

    def override_ctx(request: Request):
        return Context(
            session_factory=pg_session_factory,
            erp_engine=object(),
            pg_engine=pg_engine,
            settings=FakeSettings(),
        )

    app.dependency_overrides[get_ctx] = override_ctx
    return TestClient(app)


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


@pytest.fixture
def env(api_client, pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    _, admin_key = bootstrap_admin(f"asst_adm_{suffix}", "pw123456")
    headers = {"X-API-Key": admin_key}
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS ods.asst_order "
                "(id integer PRIMARY KEY, region text, amount numeric, cost numeric)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO ods.asst_order VALUES "
                "(1,'华东',10,5),(2,'华北',20,8),(3,'华东',30,9) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
    r = api_client.post(
        "/api/v1/admin/roles", json={"name": f"r_{suffix}"}, headers=headers
    )
    role_id = r.json()["id"]
    r = api_client.post(
        "/api/v1/admin/datasets",
        json={
            "name": f"asst_{suffix}",
            "source_table": "ods.asst_order",
            "pk_columns": ["id"],
            "columns": ["id", "region", "amount", "cost"],
        },
        headers=headers,
    )
    ds_id = r.json()["id"]
    r = api_client.post(
        "/api/v1/admin/grants",
        json={
            "role_id": role_id,
            "dataset_id": ds_id,
            "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
            "field_whitelist": ["id", "region", "amount"],
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    r = api_client.post(
        "/api/v1/admin/users",
        json={"username": f"u_{suffix}", "password": "pw123456", "role_id": role_id},
        headers=headers,
    )
    user_id = r.json()["id"]
    r = api_client.post(
        "/api/v1/admin/api-keys", json={"user_id": user_id}, headers=headers
    )
    kb = r.json()
    user_headers = {"X-API-Key": f"{kb['key_id']}.{kb['secret']}"}

    def cleanup():
        with pg_session_factory() as s:
            s.query(AuditLog).filter_by(user_id=user_id).delete()
            s.query(ApiKey).filter_by(user_id=user_id).delete()
            s.query(User).filter_by(id=user_id).delete()
            s.query(DatasetGrant).filter_by(role_id=role_id).delete()
            s.query(Role).filter_by(id=role_id).delete()
            s.query(Dataset).filter_by(id=ds_id).delete()
            adm = s.query(User).filter_by(username=f"asst_adm_{suffix}").first()
            if adm is not None:
                s.query(ApiKey).filter_by(user_id=adm.id).delete()
                s.delete(adm)
            s.commit()

    return {
        "api_client": api_client,
        "user_headers": user_headers,
        "ds_name": f"asst_{suffix}",
        "cleanup": cleanup,
    }


def test_chat_scoped_select(env, monkeypatch):
    monkeypatch.setattr(
        "app.assistant.chat_api.generate_sql",
        lambda sys_prompt, usr_prompt: "SELECT a.id, a.amount FROM ods.asst_order a",
    )
    r = env["api_client"].post(
        "/api/v1/assistant/chat",
        headers=env["user_headers"],
        json={"question": "看华东订单"},
    )
    assert r.status_code == 200
    events = _parse_sse(r.text)
    result = next(e for e in events if e["type"] == "result")
    assert result["row_count"] == 2  # 行级规则：只看华东
    assert result["columns"] == ["id", "amount"]


def test_chat_rejects_hidden_column(env, monkeypatch):
    monkeypatch.setattr(
        "app.assistant.chat_api.generate_sql",
        lambda sys_prompt, usr_prompt: "SELECT a.cost FROM ods.asst_order a",
    )
    r = env["api_client"].post(
        "/api/v1/assistant/chat",
        headers=env["user_headers"],
        json={"question": "看成本"},
    )
    events = _parse_sse(r.text)
    assert any(e["type"] == "error" for e in events)
