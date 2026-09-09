from sqlalchemy import inspect

from app.models import SyncHistory, SyncTask


def test_metadata_tables_created(pg_engine):
    names = inspect(pg_engine).get_table_names()
    assert "sync_tasks" in names
    assert "sync_history" in names


def test_insert_task_roundtrip(pg_session_factory):
    with pg_session_factory() as s:
        task = SyncTask(
            name="t-roundtrip",
            source_table="sales_order",
            target_table="ods.sales_order",
            pk_columns=["id"],
            columns=["id", "region", "amount", "update_time"],
            incremental_field="update_time",
            cron="*/15 * * * *",
        )
        s.add(task)
        s.commit()
        s.expire_all()
        loaded = s.query(SyncTask).filter_by(name="t-roundtrip").one()
        assert loaded.pk_columns == ["id"]
        assert loaded.watermark is None
        assert s.query(SyncHistory).count() == 0
        s.delete(loaded)
        s.commit()
