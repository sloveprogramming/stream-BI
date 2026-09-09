import pytest

from app.sync.retry import run_with_retry


def test_success_first_try():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    assert run_with_retry(fn, attempts=3, base_delay=0.0) == "ok"
    assert len(calls) == 1


def test_retry_then_success_with_exponential_backoff():
    state = {"n": 0}
    sleeps: list[float] = []

    def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("flaky")
        return "done"

    result = run_with_retry(fn, attempts=3, base_delay=2.0, sleep=sleeps.append)
    assert result == "done"
    assert sleeps == [2.0, 4.0]


def test_exhausted_raises_last_error():
    def fn():
        raise ValueError("always")

    with pytest.raises(ValueError, match="always"):
        run_with_retry(fn, attempts=2, base_delay=0.0)
