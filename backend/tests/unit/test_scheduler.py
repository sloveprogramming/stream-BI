from apscheduler.schedulers.background import BackgroundScheduler

from app.db import make_engine, make_session_factory
from app.models import SyncTask
from app.scheduler import execute_task, refresh_jobs
from tests.conftest import PG_URL


def _task(name, enabled, cron="0 8 * * *"):
    return SyncTask(
        name=name,
        source_table="sales_order",
        target_table="ods.sales_order",
        pk_columns=["id"],
        columns=["id", "region", "amount", "update_time"],
        incremental_field="update_time",
        cron=cron,
        enabled=enabled,
    )


def test_refresh_jobs_registers_enabled_and_removes_disabled(pg_session_factory):
    scheduler = BackgroundScheduler(timezone="Asia/Hong_Kong")
    scheduler.start()
    try:
        with pg_session_factory() as s:
            on = _task("sch-on", enabled=True)
            off = _task("sch-off", enabled=False, cron="0 9 * * *")
            s.add_all([on, off])
            s.commit()
            refresh_jobs(scheduler, pg_session_factory)
            ids = {j.id for j in scheduler.get_jobs()}
            assert f"sync_{on.id}" in ids
            assert f"sync_{off.id}" not in ids
            s.query(SyncTask).filter(SyncTask.id.in_([on.id, off.id])).delete(
                synchronize_session=False
            )
            s.commit()
    finally:
        scheduler.shutdown(wait=False)


def test_execute_task_disabled_is_noop(pg_session_factory, monkeypatch):
    with pg_session_factory() as s:
        t = _task("sch-noop", enabled=False)
        s.add(t)
        s.commit()
        tid = t.id

    class DummyEngine:
        def dispose(self):
            pass

    class FakeSettings:
        sync_retry_attempts = 1
        sync_retry_base_delay = 0.0
        sync_batch_size = 5000
        alert_webhook_url = ""

    local_pg = make_engine(PG_URL)
    local_sf = make_session_factory(local_pg)
    monkeypatch.setattr(
        "app.scheduler.job_context",
        lambda: (local_sf, DummyEngine(), local_pg, FakeSettings()),
    )

    def fail_run_sync(*args, **kwargs):
        raise AssertionError("停用任务不应执行同步")

    monkeypatch.setattr("app.scheduler.run_sync", fail_run_sync)
    execute_task(tid, "manual")  # 不抛异常即通过

    with pg_session_factory() as s:
        s.query(SyncTask).filter_by(id=tid).delete(synchronize_session=False)
        s.commit()
