import pytest
import sqlalchemy as sa

from app.models import SyncHistory, SyncTask
from app.sync.engine import run_sync
from app.sync.retry import run_with_retry
from tests.conftest import PG_URL


@pytest.fixture(scope="module")
def erp_mysql(mysql_erp_engine):
    return mysql_erp_engine


class FlakyEngine:
    """前 fail_times 次 connect 抛错，之后透传真实引擎。"""

    def __init__(self, real, fail_times=1):
        self.real = real
        self.fail_times = fail_times
        self.calls = 0

    def connect(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("erp temporarily down")
        return self.real.connect()

    def dispose(self):
        pass

    def __getattr__(self, name):
        # 透传真实引擎属性（如 dialect），保证 run_sync 内部通用逻辑可用
        return getattr(self.real, name)


def _make_task(session):
    task = SyncTask(
        name="e2e-sales-order",
        source_table="sales_order",
        target_table="ods.sales_order_e2e",
        pk_columns=["id"],
        columns=["id", "region", "amount", "update_time"],
        incremental_field="update_time",
        cron="*/15 * * * *",
    )
    session.add(task)
    session.commit()
    return task


def test_e2e_incremental_watermark_and_retry_alert(
    pg_session_factory, pg_engine, erp_mysql, monkeypatch
):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS ods.sales_order_e2e"))

    with erp_mysql.begin() as conn:
        conn.execute(sa.text("DELETE FROM sales_order WHERE id NOT IN (1, 2, 3)"))
        conn.execute(
            sa.text(
                "INSERT INTO sales_order (id, region, amount, update_time) "
                "VALUES (101, '华东', 999.99, '2026-09-09 12:00:00')"
            )
        )

    with pg_session_factory() as session:
        # 防御：清理上次运行残留，保证可重复执行
        old = session.query(SyncTask).filter_by(name="e2e-sales-order").first()
        if old is not None:
            session.query(SyncHistory).filter_by(task_id=old.id).delete()
            session.delete(old)
            session.commit()
        task = _make_task(session)

        # ① 首次全量进入 ods，水位线推进
        r1 = run_sync(session, erp_mysql, pg_engine, task, trigger="manual")
        assert r1["status"] == "success"
        assert r1["rows_written"] >= 4
        wm1 = task.watermark
        assert wm1 is not None

        # ② 新增一行后只写差异，水位线前进
        with erp_mysql.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO sales_order (id, region, amount, update_time) "
                    "VALUES (102, '华北', 50.00, '2026-09-09 18:00:00')"
                )
            )
        r2 = run_sync(session, erp_mysql, pg_engine, task, trigger="manual")
        assert r2["rows_written"] == 1
        assert task.watermark > wm1

        # ③ 失败重试：首次 connect 失败、第二次成功；失败记录留在 SyncHistory
        flaky = FlakyEngine(erp_mysql, fail_times=1)
        result = run_with_retry(
            lambda: run_sync(session, flaky, pg_engine, task, trigger="manual"),
            attempts=2,
            base_delay=0.0,
            sleep=lambda _: None,
        )
        assert result["status"] == "success"
        # 第1次：ensure_ods_table 连接失败；重试：ensure_ods_table + iter_rows 各一次\n        assert flaky.calls == 3
        failed = (
            session.query(SyncHistory)
            .filter_by(task_id=task.id, status="failed")
            .order_by(SyncHistory.id.desc())
            .first()
        )
        assert failed is not None and "temporarily down" in (failed.error or "")
        assert task.watermark >= wm1

        # ④ 最终失败触发 webhook 告警（打桩断言）
        from app import scheduler as scheduler_module
        from app.db import make_engine, make_session_factory

        alerts: list = []

        class DeadEngine:
            def connect(self):
                raise RuntimeError("erp down")

            def dispose(self):
                pass

        class FakeSettings:
            sync_retry_attempts = 2
            sync_retry_base_delay = 0.0
            sync_batch_size = 5000
            alert_webhook_url = "http://hook.local/x"

        local_pg = make_engine(PG_URL)
        local_sf = make_session_factory(local_pg)
        monkeypatch.setattr(
            scheduler_module,
            "job_context",
            lambda: (local_sf, DeadEngine(), local_pg, FakeSettings()),
        )
        monkeypatch.setattr(
            scheduler_module, "send_alert", lambda *a, **k: alerts.append(a) or True
        )
        scheduler_module.execute_task(task.id, "manual")
        assert len(alerts) == 1

        # 清理
        session.query(SyncHistory).filter_by(task_id=task.id).delete()
        session.delete(task)
        session.commit()



