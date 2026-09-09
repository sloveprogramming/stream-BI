import pytest

from app.access.scoped_query import build_scoped_sql


class _Dataset:
    name = "sales_order"
    source_table = "ods.sales_order"
    pk_columns = ["id"]
    columns = ["id", "region", "amount", "cost"]


class _Grant:
    row_rules = [{"field": "region", "op": "eq", "value": "华东"}]
    field_whitelist = ["id", "region", "amount"]


def test_whitelist_and_row_rules_applied():
    sql, params = build_scoped_sql(_Dataset(), _Grant(), {}, 50, 0)
    assert sql.count("SELECT") == 1
    assert '"id"' in sql and '"amount"' in sql
    assert '"cost"' not in sql
    assert '"region" = :r_0' in sql
    assert params["r_0"] == "华东"
    assert "LIMIT :limit OFFSET :offset" in sql


def test_null_whitelist_means_all_fields():
    class G(_Grant):
        field_whitelist = None

    sql, _ = build_scoped_sql(_Dataset(), G(), {}, 50, 0)
    assert '"cost"' in sql


def test_user_filters_parameterized():
    sql, params = build_scoped_sql(_Dataset(), _Grant(), {"amount": 100}, 50, 0)
    assert '"amount" = :f_amount' in sql
    assert params["f_amount"] == 100


def test_invalid_identifier_rejected():
    class EvilGrant(_Grant):
        field_whitelist = None

    class Evil(_Dataset):
        columns = ["id; DROP TABLE users"]

    with pytest.raises(ValueError):
        build_scoped_sql(Evil(), EvilGrant(), {}, 50, 0)


def test_unknown_filter_field_rejected():
    with pytest.raises(ValueError):
        build_scoped_sql(_Dataset(), _Grant(), {"hacker": 1}, 50, 0)


def test_order_by_pk_and_count_pair():
    sql, _ = build_scoped_sql(_Dataset(), _Grant(), {}, 50, 0)
    assert "ORDER BY" in sql and '"id"' in sql

