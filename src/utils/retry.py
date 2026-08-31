"""
src/utils/retry.py
------------------
Async-compatible retry decorator with exponential backoff and jitter.

Used on all DB and Redis calls to survive transient connection blips
without crashing the request handler.

Usage
-----
@retry(max_attempts=3, base_delay=0.1, exceptions=(asyncpg.PostgresConnectionError,))
async def fetch_url(short_code: str) -> str:
    ...
"""

from __future__ import annotations

import asyncio
import functools
import random
from typing import Callable, Tuple, Type

from src.utils.logger import get_logger

log = get_logger(__name__)


def retry(
    max_attempts: int = 3,
    base_delay: float = 0.1,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """
    Decorator that retries an async function on specified exceptions.

    Parameters
    ----------
    max_attempts  : Total calls including the first (default 3).
    base_delay    : Seconds to wait before first retry (default 0.1).
    backoff_factor: Multiplier per retry (default 2.0 → 0.1s, 0.2s, 0.4s).
    jitter        : Add ±25% random jitter to prevent thundering herd.
    exceptions    : Which exception types trigger a retry.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        log.error(
                            "Retry exhausted",
                            extra={
                                "fn":      fn.__qualname__,
                                "attempt": attempt,
                                "error":   str(exc),
                            }
                        )
                        raise

                    delay = base_delay * (backoff_factor ** (attempt - 1))
                    if jitter:
                        delay *= (0.75 + random.random() * 0.5)

                    log.warning(
                        "Retrying after error",
                        extra={
                            "fn":       fn.__qualname__,
                            "attempt":  attempt,
                            "delay_s":  round(delay, 3),
                            "error":    str(exc),
                        }
                    )
                    await asyncio.sleep(delay)

            raise last_exc  # unreachable but satisfies type checkers

        return wrapper
    return decorator
