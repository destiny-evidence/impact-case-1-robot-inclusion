import asyncio
import time
from collections.abc import Generator
from contextlib import contextmanager


@contextmanager
def measure_runtime() -> Generator[float]:
    """Yield an ``ElapsedSeconds`` model with ``seconds`` set on context exit."""
    elapsed: float = 0
    start = time.perf_counter()
    try:
        yield elapsed
    finally:
        elapsed = round(time.perf_counter() - start, 3)


class RateLimiter:
    """Limits calls to `rate` per `period` seconds (e.g. rate=60, period=60 -> 60/min)."""

    def __init__(self, rate: int, period: float = 60.0) -> None:
        self.rate = rate
        self.period = period
        self._tokens = float(rate)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._tokens = min(self.rate, self._tokens + elapsed * (self.rate / self.period))
                self._updated_at = now

                if self._tokens >= 1:
                    self._tokens -= 1
                    return

                wait_time = (1 - self._tokens) * (self.period / self.rate)

            await asyncio.sleep(wait_time)
