from app.assistant.sql_rewrite import rewrite_sql

T = [
    {
        "name": "o",
        "source_table": "ods.order",
        "columns": ["id", "product_id", "qty"],
        "row_rules": [{"field": "region", "op": "eq", "value": "华东"}],
    },
    {
        "name": "s",
        "source_table": "ods.stock",
        "columns": ["product_id", "stock"],
        "row_rules": [],
    },
]


def test_rewrite_injects_row_filter_for_join():
    sql = "SELECT o.id, s.stock FROM ods.order o JOIN ods.stock s ON o.product_id = s.product_id"
    out, params = rewrite_sql(sql, T)
    assert 'WHERE "region" = :r_0_0' in out
    assert params == {"r_0_0": "华东"}
    assert "ods.order" in out and "ods.stock" in out


def test_whitelist_columns_only_in_subquery():
    sql = "SELECT * FROM ods.order"
    out, _ = rewrite_sql(sql, T)
    assert '"product_id"' in out
    assert '"qty"' in out


def test_table_without_rules_has_no_where():
    out, params = rewrite_sql("SELECT s.stock FROM ods.stock s", T)
    assert "WHERE" not in out
    assert params == {}
