from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_transient(
    operation: Callable[[], T],
    *,
    retries: int = 3,
    base_delay: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run once plus up to ``retries`` retries for transient failures."""
    for attempt in range(retries + 1):
        try:
            return operation()
        except Exception as exc:
            if attempt >= retries or not is_transient(exc):
                raise
            delay = base_delay * (2**attempt) + random.uniform(0, base_delay / 4)
            sleep(delay)
    raise AssertionError("unreachable")


def retry_quality(operation: Callable[[], T], *, retries: int = 2) -> T:
    """Run once plus up to ``retries`` retries for quality failures."""
    from .errors import QualityError

    for attempt in range(retries + 1):
        try:
            return operation()
        except QualityError:
            if attempt >= retries:
                raise
    raise AssertionError("unreachable")


def is_transient(exc: Exception) -> bool:
    from .errors import TransientError

    if isinstance(exc, TransientError):
        return True
    try:
        import httpx

        return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
    except ImportError:
        return False
