import pytest

from app.access.ratelimit import FixedWindowLimiter, RateLimitExceeded


def test_allows_within_limit():
    lim = FixedWindowLimiter(limit_per_minute=3, limit_per_day=100)
    for _ in range(3):
        lim.check("k1")


def test_minute_limit_exceeded():
    lim = FixedWindowLimiter(limit_per_minute=2, limit_per_day=100)
    lim.check("k1")
    lim.check("k1")
    with pytest.raises(RateLimitExceeded):
        lim.check("k1")


def test_keys_isolated():
    lim = FixedWindowLimiter(limit_per_minute=1, limit_per_day=100)
    lim.check("k1")
    lim.check("k2")  # 不抛
