from datetime import datetime, timezone

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import Context, get_ctx
from app.main import create_app
from app.models import SyncHistory, SyncTask


class FakeSettings:
    sync_retry_attempts = 1
    sync_retry_base_delay = 0.0
    sync_batch_size = 5000
    alert_webhook_url = ""


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


def test_list_and_history(api_client, pg_session_factory):
    with pg_session_factory() as s:
        # 防御：清理上次运行可能残留的数据，保证可重复执行
        s.query(SyncHistory).filter(
            SyncHistory.task_id.in_(s.query(SyncTask.id).filter_by(name="api-t"))
        ).delete(synchronize_session=False)
        s.query(SyncTask).filter_by(name="api-t").delete(synchronize_session=False)
        s.commit()
        t = SyncTask(
            name="api-t",
            source_table="sales_order",
            target_table="ods.api_t",
            pk_columns=["id"],
            columns=["id"],
            incremental_field=None,
            cron="0 8 * * *",
        )
        s.add(t)
        s.commit()
        s.add(
            SyncHistory(
                task_id=t.id,
                trigger="manual",
                status="success",
                finished_at=datetime.now(timezone.utc),
                rows_pulled=3,
                rows_written=3,
            )
        )
        s.commit()
        tid = t.id

    resp = api_client.get("/api/v1/sync/tasks")
    assert resp.status_code == 200
    assert any(item["id"] == tid for item in resp.json())

    resp = api_client.get(f"/api/v1/sync/tasks/{tid}/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["status"] == "success"
    assert body[0]["rows_pulled"] == 3

    assert api_client.get("/api/v1/sync/tasks/999999/history").status_code == 404

    with pg_session_factory() as s:
        s.query(SyncHistory).filter_by(task_id=tid).delete()
        s.query(SyncTask).filter_by(id=tid).delete()
        s.commit()


