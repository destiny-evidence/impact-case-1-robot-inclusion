import time
from contextlib import contextmanager
from typing import Generator

from destiny_sdk.references import Reference
from destiny_sdk.enhancements import AbstractContentEnhancement


def get_abstract(reference: Reference) -> str | None:
    """Extract the abstract from a reference."""
    for enhancement in reference.enhancements or []:
        if isinstance(enhancement.content, AbstractContentEnhancement):
            return enhancement.content.abstract
    return None


@contextmanager
def measure_runtime() -> Generator[float, None, None]:
    """Yield an ``ElapsedSeconds`` model with ``seconds`` set on context exit."""
    elapsed: float = 0
    start = time.perf_counter()
    try:
        yield elapsed
    finally:
        elapsed = round(time.perf_counter() - start, 3)
