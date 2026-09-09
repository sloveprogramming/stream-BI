import time


class RateLimitExceeded(Exception):
    pass


class FixedWindowLimiter:
    """进程内固定窗口限流。重启清零：P2 单机规模可接受（见 P2 计划全局约束）。"""

    def __init__(self, limit_per_minute: int, limit_per_day: int):
        self.limit_per_minute = limit_per_minute
        self.limit_per_day = limit_per_day
        self._minute: dict[str, tuple[int, int]] = {}
        self._day: dict[str, tuple[int, int]] = {}

    def check(self, key_id: str) -> None:
        now = time.time()
        m_slot = int(now // 60)
        d_slot = int(now // 86400)
        m_slot_, m_count = self._minute.get(key_id, (m_slot, 0))
        if m_slot_ != m_slot:
            m_slot_, m_count = m_slot, 0
        if m_count + 1 > self.limit_per_minute:
            raise RateLimitExceeded("minute limit exceeded")
        d_slot_, d_count = self._day.get(key_id, (d_slot, 0))
        if d_slot_ != d_slot:
            d_slot_, d_count = d_slot, 0
        if d_count + 1 > self.limit_per_day:
            raise RateLimitExceeded("daily limit exceeded")
        self._minute[key_id] = (m_slot_, m_count + 1)
        self._day[key_id] = (d_slot_, d_count + 1)


class LimiterHolder:
    _limiter = None

    @classmethod
    def get(cls, settings):
        if cls._limiter is None:
            cls._limiter = FixedWindowLimiter(
                settings.rate_limit_per_minute, settings.rate_limit_per_day
            )
        return cls._limiter
