"""ERP 自动同步的决策逻辑单元测试（纯函数，不依赖数据库）。"""
from app.sync.erp_autosync import pick_incremental_field, plan_sync_tasks


def test_creates_task_and_detects_incremental():
    plan = plan_sync_tasks(
        tables=["shop"],
        columns={"shop": ["shop_id", "shop_name", "modified", "_synced_at"]},
        pk_map={"shop": ["shop_id"]},
        existing_source_tables=set(),
    )
    assert [c["table"] for c in plan["create"]] == ["shop"]
    item = plan["create"][0]
    assert item["pk_columns"] == ["shop_id"]
    assert item["incremental_field"] == "modified"
    # 下划线开头的列不进数据集
    assert "_synced_at" not in item["columns"]


def test_table_without_pk_is_skipped():
    plan = plan_sync_tasks(
        tables=["log"],
        columns={"log": ["msg", "ts"]},
        pk_map={},
        existing_source_tables=set(),
    )
    assert plan["create"] == []
    assert plan["skipped_no_pk"] == ["log"]


def test_pk_missing_from_columns_is_skipped():
    # 主键字段恰好是下划线开头（被排除）时也不能建任务
    plan = plan_sync_tasks(
        tables=["t"],
        columns={"t": ["_id", "name"]},
        pk_map={"t": ["_id"]},
        existing_source_tables=set(),
    )
    assert plan["create"] == []
    assert plan["skipped_no_pk"] == ["t"]


def test_existing_task_is_not_recreated():
    plan = plan_sync_tasks(
        tables=["shop"],
        columns={"shop": ["shop_id"]},
        pk_map={"shop": ["shop_id"]},
        existing_source_tables={"shop"},
    )
    assert plan["create"] == []
    assert plan["skipped_existing"] == ["shop"]


def test_include_and_exclude_patterns():
    tables = ["wdt_order", "wdt_stock", "tmp_backup"]
    columns = {t: ["id", "modified"] for t in tables}
    pk_map = {t: ["id"] for t in tables}

    plan = plan_sync_tasks(
        tables, columns, pk_map, set(), include="wdt_*", exclude="*_stock"
    )
    assert [c["table"] for c in plan["create"]] == ["wdt_order"]

    plan2 = plan_sync_tasks(tables, columns, pk_map, set(), include="")
    assert [c["table"] for c in plan2["create"]] == tables


def test_max_tables_cap():
    tables = [f"t{i}" for i in range(10)]
    columns = {t: ["id"] for t in tables}
    pk_map = {t: ["id"] for t in tables}
    plan = plan_sync_tasks(tables, columns, pk_map, set(), max_tables=3)
    assert len(plan["create"]) == 3
    assert plan["capped"] is True


def test_pick_incremental_field_variants():
    assert pick_incremental_field(["id", "update_time"]) == "update_time"
    assert pick_incremental_field(["id", "GMT_MODIFIED"]) == "GMT_MODIFIED"
    assert pick_incremental_field(["id", "order_modified"]) == "order_modified"
    # 形如 modified_xxx / modified_at 也认
    assert pick_incremental_field(["id", "modified_at"]) == "modified_at"
    # created_at 单调递增但语义是"创建"，不作为增量字段；改成全量比对更安全
    assert pick_incremental_field(["id", "created"]) is None
    assert pick_incremental_field(["id", "name"]) is None


def test_no_incremental_field_means_full_compare():
    plan = plan_sync_tasks(
        tables=["dict"],
        columns={"dict": ["code", "name"]},
        pk_map={"dict": ["code"]},
        existing_source_tables=set(),
    )
    assert plan["create"][0]["incremental_field"] is None
