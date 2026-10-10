"""Step 3a：schema 上下文渲染 + ERP 字段注释回填的测试。"""
import uuid

import sqlalchemy as sa

from app.assistant.schema_context import _render_prompt, build_schema_context
from app.sync import erp_autosync


def _table(**kw):
    base = {
        "name": "sales_order",
        "title": "销售订单",
        "description": None,
        "columns": ["id", "region", "amount"],
        "column_comments": {},
        "pk_columns": ["id"],
        "samples": [],
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- 渲染


def test_render_inlines_column_comments():
    prompt = _render_prompt([_table(column_comments={"amount": "金额（元）"})])
    assert "amount（金额（元））" in prompt
    # 没注释的列保持原样
    assert "id," in prompt


def test_render_expands_all_samples_as_key_value():
    prompt = _render_prompt(
        [_table(samples=[["1", "华东", "10"], ["2", "华北", "20"], ["3", "华南", "30"]])]
    )
    assert "id=1, region=华东, amount=10" in prompt
    assert "id=2, region=华北, amount=20" in prompt
    assert "id=3, region=华南, amount=30" in prompt


def test_render_falls_back_to_plain_columns_without_comments():
    prompt = _render_prompt([_table()])
    assert "字段：id, region, amount" in prompt


def test_render_tells_model_to_use_dataset_name_not_ods_prefix():
    prompt = _render_prompt([_table()])
    assert "不要加 ods." in prompt


def test_render_omits_comments_when_schema_is_wide():
    columns = [f"c{i}" for i in range(60)]
    prompt = _render_prompt([_table(columns=columns, column_comments={"c1": "注释"})])
    assert "字段：c0, c1," in prompt
    assert "（注释）" not in prompt


def test_render_includes_title_description_and_pk():
    prompt = _render_prompt(
        [_table(description="ERP 同步自动发现", pk_columns=["id", "order_no"])]
    )
    assert "表 sales_order —— 销售订单" in prompt
    assert "说明：ERP 同步自动发现" in prompt
    assert "主键：id, order_no" in prompt


def test_render_handles_empty_table_list():
    assert _render_prompt([]).startswith("可用数据集如下")


# ---------------------------------------------------------------- 端到端：注释进 prompt


def test_schema_context_carries_dataset_comments(pg_session_factory, pg_engine):
    suffix = uuid.uuid4().hex[:8]
    table = f"ods.cc_{suffix}"
    with pg_engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(
            sa.text(f"CREATE TABLE IF NOT EXISTS {table} (id integer PRIMARY KEY, amount numeric)")
        )
        conn.execute(sa.text(f"DELETE FROM {table}"))
        conn.execute(sa.text(f"INSERT INTO {table} VALUES (1, 10.5), (2, 20.5)"))

    from app.models import Dataset, User

    with pg_session_factory() as s:
        user = User(username=f"cc_u_{suffix}", password_hash="x", is_admin=True)
        dataset = Dataset(
            name=f"cc_d_{suffix}",
            title="测试表",
            source_table=table,
            pk_columns=["id"],
            columns=["id", "amount"],
            column_comments={"amount": "金额（元）"},
        )
        s.add_all([user, dataset])
        s.commit()
        user_id, dataset_id = user.id, dataset.id

    try:
        with pg_session_factory() as s:
            user = s.get(User, user_id)
            ctx = build_schema_context(pg_session_factory, pg_engine, user)
        entry = next(t for t in ctx["tables"] if t["name"] == f"cc_d_{suffix}")
        assert entry["column_comments"] == {"amount": "金额（元）"}
        assert "amount（金额（元））" in ctx["prompt"]
        assert "id=1, amount=10.5" in ctx["prompt"]
    finally:
        with pg_session_factory() as s:
            s.query(Dataset).filter_by(id=dataset_id).delete()
            s.query(User).filter_by(id=user_id).delete()
            s.commit()


# ---------------------------------------------------------------- ERP 注释回填


def test_sync_column_comments_backfills_and_skips_unchanged(
    pg_session_factory, monkeypatch
):
    from app.models import Dataset, SyncTask

    suffix = uuid.uuid4().hex[:8]
    erp_table = f"erp_{suffix}"
    ods_table = f"ods.t_{suffix}"
    with pg_session_factory() as s:
        task = SyncTask(
            name=f"t_{suffix}",
            source_table=erp_table,
            target_table=ods_table,
            pk_columns=["id"],
            columns=["id", "amount", "note"],
            incremental_field=None,
            cron="*/30 * * * *",
        )
        dataset = Dataset(
            name=f"d_{suffix}",
            source_table=ods_table,
            pk_columns=["id"],
            columns=["id", "amount", "note"],
        )
        s.add_all([task, dataset])
        s.commit()
        task_id, dataset_id = task.id, dataset.id

    monkeypatch.setattr(
        erp_autosync,
        "scan_column_comments",
        lambda engine: {erp_table: {"amount": "金额", "note": "备注", "不存在的列": "x"}},
    )
    try:
        stats = erp_autosync.sync_column_comments(object(), pg_session_factory)
        assert stats["updated_datasets"] == 1
        with pg_session_factory() as s:
            ds = s.get(Dataset, dataset_id)
            # 只回填数据集里真实存在的列
            assert ds.column_comments == {"amount": "金额", "note": "备注"}

        # 内容没变 → 不再写库
        stats2 = erp_autosync.sync_column_comments(object(), pg_session_factory)
        assert stats2["updated_datasets"] == 0
    finally:
        with pg_session_factory() as s:
            s.query(Dataset).filter_by(id=dataset_id).delete()
            s.query(SyncTask).filter_by(id=task_id).delete()
            s.commit()


def test_sync_column_comments_noop_when_no_comments(pg_session_factory, monkeypatch):
    monkeypatch.setattr(erp_autosync, "scan_column_comments", lambda engine: {})
    assert erp_autosync.sync_column_comments(object(), pg_session_factory) == {
        "commented_tables": 0,
        "updated_datasets": 0,
    }
