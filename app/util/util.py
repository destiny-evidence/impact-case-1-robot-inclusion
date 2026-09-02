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
