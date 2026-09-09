from app.sync.writer import upsert_rows


def test_upsert_rows_empty_returns_zero():
    assert upsert_rows(None, "ods.t", [], ["id"]) == 0
