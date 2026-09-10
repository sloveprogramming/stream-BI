from datetime import datetime

from app.sync.engine import _max_incremental


def test_max_incremental_ignores_nulls():
    batch = [
        {"modified": None},
        {"modified": datetime(2026, 7, 1, 10, 0)},
        {"modified": datetime(2026, 7, 2, 9, 0)},
    ]
    assert _max_incremental(batch, "modified") == datetime(2026, 7, 2, 9, 0)


def test_max_incremental_all_nulls_returns_none():
    batch = [{"modified": None}, {"modified": None}]
    assert _max_incremental(batch, "modified") is None
