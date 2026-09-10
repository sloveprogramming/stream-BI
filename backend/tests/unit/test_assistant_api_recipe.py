"""接口方案反解（api_recipe）单元测试：纯逻辑，不依赖数据库。"""
from app.assistant.api_recipe import build_api_recipe

TABLES = [
    {
        "name": "wdt_stockout_orders",
        "title": "出库单",
        "source_table": "ods.wdt_stockout_orders",
        "columns": ["id", "region", "amount", "created_at"],
        "pk_columns": ["id"],
    },
    {
        "name": "wdt_detail",
        "source_table": "ods.wdt_stockout_order_details",
        "columns": ["id", "order_id", "sku"],
        "pk_columns": ["id"],
    },
]


def test_single_table_equality_maps_to_api():
    r = build_api_recipe(
        "SELECT id, region FROM ods.wdt_stockout_orders WHERE region = '华东' LIMIT 100",
        TABLES,
    )
    assert r["callable"] is True
    assert r["dataset"] == "wdt_stockout_orders"
    assert r["endpoint"] == "/api/v1/datasets/wdt_stockout_orders/rows"
    assert r["params"] == {"region": "华东", "limit": "100"}
    assert r["mcp"]["arguments"]["filters"] == {"region": "华东"}


def test_multiple_equalities_and_offset():
    r = build_api_recipe(
        "SELECT * FROM ods.wdt_stockout_orders "
        "WHERE region = '华东' AND amount = 10 LIMIT 10 OFFSET 20",
        TABLES,
    )
    assert r["callable"] is True
    assert r["filters"] == {"region": "华东", "amount": "10"}
    assert r["limit"] == 10
    assert r["offset"] == 20
    assert r["params"]["offset"] == "20"


def test_filter_field_not_in_columns_not_callable():
    r = build_api_recipe(
        "SELECT * FROM ods.wdt_stockout_orders WHERE not_a_column = '1'", TABLES
    )
    assert r["callable"] is False


def test_no_limit_clamps_to_rest_cap():
    r = build_api_recipe("SELECT * FROM ods.wdt_stockout_orders", TABLES)
    assert r["callable"] is True
    assert r["limit"] == 500
    assert any("没有 LIMIT" in c for c in r["caveats"])


def test_limit_over_cap_is_clamped_with_caveat():
    r = build_api_recipe(
        "SELECT * FROM ods.wdt_stockout_orders LIMIT 5000", TABLES
    )
    assert r["limit"] == 500
    assert any("超过接口上限" in c for c in r["caveats"])


def test_order_by_pk_ok_without_caveat():
    r = build_api_recipe(
        "SELECT * FROM ods.wdt_stockout_orders ORDER BY id LIMIT 10", TABLES
    )
    assert r["callable"] is True
    assert not any("顺序" in c for c in r["caveats"])


def test_order_by_non_pk_without_limit_keeps_caveat():
    r = build_api_recipe(
        "SELECT * FROM ods.wdt_stockout_orders ORDER BY amount DESC", TABLES
    )
    assert r["callable"] is True
    assert any("顺序" in c for c in r["caveats"])


def test_topn_not_callable():
    r = build_api_recipe(
        "SELECT * FROM ods.wdt_stockout_orders ORDER BY amount DESC LIMIT 5", TABLES
    )
    assert r["callable"] is False
    assert "TopN" in r["reason"]


def test_join_not_callable():
    r = build_api_recipe(
        "SELECT a.id FROM ods.wdt_stockout_orders a "
        "JOIN ods.wdt_stockout_order_details b ON a.id = b.order_id",
        TABLES,
    )
    assert r["callable"] is False
    assert "JOIN" in r["reason"]


def test_group_by_not_callable():
    r = build_api_recipe(
        "SELECT region, SUM(amount) FROM ods.wdt_stockout_orders GROUP BY region",
        TABLES,
    )
    assert r["callable"] is False


def test_aggregate_without_group_not_callable():
    r = build_api_recipe("SELECT COUNT(*) FROM ods.wdt_stockout_orders", TABLES)
    assert r["callable"] is False


def test_range_predicate_not_callable():
    r = build_api_recipe(
        "SELECT * FROM ods.wdt_stockout_orders WHERE amount > 100", TABLES
    )
    assert r["callable"] is False


def test_or_predicate_not_callable():
    r = build_api_recipe(
        "SELECT * FROM ods.wdt_stockout_orders "
        "WHERE region = '华东' OR region = '华北'",
        TABLES,
    )
    assert r["callable"] is False


def test_unknown_table_not_callable():
    r = build_api_recipe("SELECT * FROM ods.nope LIMIT 5", TABLES)
    assert r["callable"] is False
    assert "数据集" in r["reason"]


def test_unparsable_sql_gives_no_recipe():
    # sqlglot 很宽容，垃圾输入可能被解析成非 Select；此时要么 None，要么明确不可对应
    r = build_api_recipe("this is not sql", TABLES)
    assert r is None or r.get("callable") is False
