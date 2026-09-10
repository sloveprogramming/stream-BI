import pytest

from app.assistant.sql_guard import SqlGuardError, validate_sql

T = [
    {"name": "o", "source_table": "ods.order", "columns": ["id", "product_id", "qty"]},
    {"name": "s", "source_table": "ods.stock", "columns": ["product_id", "stock"]},
]


def test_allows_select_join():
    validate_sql(
        "SELECT o.id, s.stock FROM ods.order o "
        "JOIN ods.stock s ON o.product_id = s.product_id",
        T,
    )


def test_rejects_insert():
    with pytest.raises(SqlGuardError):
        validate_sql("INSERT INTO ods.order VALUES (1, 2, 3)", T)


def test_rejects_unknown_table():
    with pytest.raises(SqlGuardError, match="无权限的表"):
        validate_sql("SELECT * FROM ods.secret", T)


def test_rejects_hidden_column():
    with pytest.raises(SqlGuardError, match="无权限的字段"):
        validate_sql("SELECT id, cost FROM ods.order", T)


def test_rejects_semicolon_injection():
    with pytest.raises(SqlGuardError):
        validate_sql("SELECT id FROM ods.order; DROP TABLE ods.order", T)


def test_allows_trailing_semicolon():
    validate_sql("SELECT id FROM ods.order;", T)


def test_allows_alias_in_order_by():
    validate_sql(
        "SELECT o.id, COUNT(s.stock) AS detail_count "
        "FROM ods.order o JOIN ods.stock s ON o.product_id = s.product_id "
        "GROUP BY o.id ORDER BY detail_count DESC",
        T,
    )


def test_rejects_non_select():
    with pytest.raises(SqlGuardError):
        validate_sql("UPDATE ods.order SET qty = 0", T)
