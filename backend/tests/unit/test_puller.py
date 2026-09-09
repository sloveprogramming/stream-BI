import sqlalchemy as sa

from app.sync.puller import build_select_sql, iter_rows
from app.sync.spec import SyncSpec

SPEC = SyncSpec(
    source_table="sales_order",
    columns=["id", "region", "amount"],
    pk_columns=["id"],
    incremental_field=None,
)


def test_quote_ident_rejects_injection():
    from app.sync.puller import quote_ident
    import pytest

    with pytest.raises(ValueError):
        quote_ident("id; DROP TABLE x")
    with pytest.raises(ValueError):
        quote_ident("1abc")


def test_build_select_sql_with_watermark():
    from datetime import datetime

    spec = SyncSpec(
        source_table="sales_order",
        columns=["id", "region", "amount", "update_time"],
        pk_columns=["id"],
        incremental_field="update_time",
    )
    sql, params = build_select_sql(spec, datetime(2026, 9, 1))
    assert "WHERE update_time > :watermark" in sql
    assert "ORDER BY id" in sql
    assert params["watermark"] == datetime(2026, 9, 1)


def test_iter_rows_batches():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE sales_order (id INT, region TEXT, amount REAL)"))
        conn.execute(
            sa.text("INSERT INTO sales_order VALUES (1,'华东',10.0),(2,'华北',20.0),(3,'华南',30.0)")
        )
    batches = list(iter_rows(engine, SPEC, None, batch_size=2))
    assert [len(b) for b in batches] == [2, 1]
    assert batches[0][0] == {"id": 1, "region": "华东", "amount": 10.0}
