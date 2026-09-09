import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def run_with_retry(
    fn: Callable[[], T],
    attempts: int = 3,
    base_delay: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run fn, retrying on failure with exponential backoff.

    Sleeps base_delay * 2**(i-1) before the i-th retry; re-raises the
    last exception once attempts are exhausted.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if i < attempts - 1:
                sleep(base_delay * (2**i))
    assert last_exc is not None
    raise last_exc

