"""结构演进单元测试（纯函数）+ 集成测试（真实 PG，ERP 元数据用假数据）。"""
import uuid

import pytest
import sqlalchemy as sa

from app.db import make_session_factory
from app.models import Dataset, SyncTask
from app.sync.evolution import (
    evolve_sync_schemas,
    missing_columns,
    removed_columns,
)


def test_missing_columns_ignores_internal_and_known():
    assert missing_columns(
        ["id", "name"], ["id", "name", "amount", "_synced_at"]
    ) == ["amount"]
    assert missing_columns([], ["id", "modified"]) == ["id", "modified"]
    assert missing_columns(["id"], ["id"]) == []


def test_removed_columns_reports_vanished():
    assert removed_columns(["id", "name", "old_col"], ["id", "name"]) == ["old_col"]
    assert removed_columns(["id"], ["id", "new"]) == []


@pytest.fixture
def evo_env(pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    table = f"wdt_evo_{suffix}"
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                f'CREATE TABLE ods."{table}" '
                "(id integer PRIMARY KEY, name text, _synced_at timestamptz DEFAULT now())"
            )
        )
    with pg_session_factory() as s:
        ds = Dataset(
            name=table,
            source_table=f"ods.{table}",
            pk_columns=["id"],
            columns=["id", "name"],
        )
        task = SyncTask(
            name=f"{table}__auto",
            source_table=table,
            target_table=f"ods.{table}",
            pk_columns=["id"],
            columns=["id", "name"],
            incremental_field=None,
            cron="*/30 * * * *",
            enabled=True,
        )
        s.add_all([ds, task])
        s.commit()
        ds_id, task_id = ds.id, task.id
    yield {"table": table, "ds_id": ds_id, "task_id": task_id}
    with pg_session_factory() as s:
        s.query(Dataset).filter_by(id=ds_id).delete()
        s.query(SyncTask).filter_by(id=task_id).delete()
        s.commit()
    with pg_engine.begin() as conn:
        conn.execute(sa.text(f'DROP TABLE IF EXISTS ods."{table}"'))


def test_evolve_adds_column_to_ods_task_and_dataset(
    evo_env, pg_session_factory, pg_engine, monkeypatch
):
    table = evo_env["table"]
    # ERP 侧多了一个 modified 字段
    monkeypatch.setattr(
        "app.sync.evolution.scan_erp_columns",
        lambda engine: {table: {"id": "int", "name": "varchar", "modified": "datetime"}},
    )

    result = evolve_sync_schemas(object(), pg_engine, pg_session_factory, "ods")
    assert [e["table"] for e in result["evolved"]] == [table]
    assert result["evolved"][0]["added"] == ["modified"]
    assert result["evolved"][0]["datasets"] == [table]

    # ods 表真的加上了列，且类型按 MySQL→PG 映射
    with pg_engine.connect() as conn:
        cols = {
            r[0]: r[1]
            for r in conn.execute(
                sa.text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema='ods' AND table_name=:t"
                ),
                {"t": table},
            ).all()
        }
    assert "modified" in cols
    assert cols["modified"] == "timestamp with time zone"

    with pg_session_factory() as s:
        task = s.get(SyncTask, evo_env["task_id"])
        ds = s.get(Dataset, evo_env["ds_id"])
        assert task.columns == ["id", "name", "modified"]
        assert ds.columns == ["id", "name", "modified"]

    # 幂等：第二次没有可演进的内容
    second = evolve_sync_schemas(object(), pg_engine, pg_session_factory, "ods")
    assert second["evolved"] == []


def test_evolve_warns_on_removed_column_and_keeps_it(
    evo_env, pg_session_factory, pg_engine, monkeypatch
):
    table = evo_env["table"]
    # ERP 侧少了一个字段（被删），且没有新增
    monkeypatch.setattr(
        "app.sync.evolution.scan_erp_columns",
        lambda engine: {table: {"id": "int"}},
    )
    result = evolve_sync_schemas(object(), pg_engine, pg_session_factory, "ods")
    assert result["evolved"] == []
    assert any("name" in w for w in result["warnings"])
    # 保守策略：不删列
    with pg_engine.connect() as conn:
        cols = [
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='ods' AND table_name=:t"
                ),
                {"t": table},
            ).all()
        ]
    assert "name" in cols
