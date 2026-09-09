from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from app.models import SyncTask
from app.sync.engine import run_sync

_CLEANUP_SQL = [
    "DELETE FROM sync_history WHERE task_id IN (SELECT id FROM sync_tasks WHERE name = 't-engine')",
    "DELETE FROM sync_tasks WHERE name = 't-engine'",
]


@pytest.fixture
def erp_sqlite():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE sales_order (id INT, region TEXT, amount REAL, update_time TEXT)"))
        conn.execute(sa.text("INSERT INTO sales_order VALUES (1,'华东',10.0,'2026-09-01 00:00:00')"))
        conn.execute(sa.text("INSERT INTO sales_order VALUES (2,'华北',20.0,'2026-09-02 00:00:00')"))
    return engine


@pytest.fixture
def pg_for_ods(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text("DROP TABLE IF EXISTS ods.sales_order_test"))
        for sql in _CLEANUP_SQL:  # 清理上次运行残留，保证可重复执行
            conn.execute(sa.text(sql))
    yield pg_engine
    with pg_engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS ods.sales_order_test"))
        for sql in _CLEANUP_SQL:
            conn.execute(sa.text(sql))


def _make_task(session):
    task = SyncTask(
        name="t-engine",
        source_table="sales_order",
        target_table="ods.sales_order_test",
        pk_columns=["id"],
        columns=["id", "region", "amount", "update_time"],
        incremental_field=None,  # 全量比对，避开跨库类型/时区差异
        cron="*/15 * * * *",
    )
    session.add(task)
    session.commit()
    return task


def test_full_compare_sync(pg_session_factory, erp_sqlite, pg_for_ods):
    with pg_session_factory() as session:
        task = _make_task(session)
        r1 = run_sync(session, erp_sqlite, pg_for_ods, task, trigger="manual")
        assert r1["status"] == "success" and r1["rows_written"] == 2
        # 重复跑：无差异，不写行
        r2 = run_sync(session, erp_sqlite, pg_for_ods, task, trigger="manual")
        assert r2["rows_written"] == 0
        # 变更一行 + 新增一行
        with erp_sqlite.begin() as conn:
            conn.execute(sa.text("UPDATE sales_order SET amount=99.0 WHERE id=1"))
            conn.execute(sa.text("INSERT INTO sales_order VALUES (3,'华南',30.0,'2026-09-03 00:00:00')"))
        r3 = run_sync(session, erp_sqlite, pg_for_ods, task, trigger="manual")
        assert r3["rows_written"] == 2
        with pg_for_ods.connect() as conn:
            amount = conn.execute(
                sa.text("SELECT amount FROM ods.sales_order_test WHERE id=1")
            ).scalar_one()
        assert float(amount) == 99.0
