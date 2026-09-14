"""A polling loop that backs off between checks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Final, TypedDict

DEFAULT_INITIAL_INTERVAL_MS: Final = 100
DEFAULT_MAX_INTERVAL_MS: Final = 1000
DEFAULT_BACKOFF_FACTOR: Final = 1.5


class PollIntervals(TypedDict):
    """The pair of bounds :func:`poll_until` backs off between."""

    initial_interval_ms: float
    max_interval_ms: float


def resolve_poll_interval(
    poll_interval_ms: float | None,
    max_interval_ms: float = DEFAULT_MAX_INTERVAL_MS,
) -> PollIntervals:
    """Turn a caller-supplied ``poll_interval_ms`` into poll bounds.

    Omitting it backs off; an explicit number pins the delay to exactly that.
    """
    if poll_interval_ms is None:
        return {
            "initial_interval_ms": DEFAULT_INITIAL_INTERVAL_MS,
            "max_interval_ms": max_interval_ms,
        }

    return {
        "initial_interval_ms": poll_interval_ms,
        "max_interval_ms": poll_interval_ms,
    }


async def poll_until(
    check: Callable[[], Awaitable[bool]],
    *,
    initial_interval_ms: float = DEFAULT_INITIAL_INTERVAL_MS,
    max_interval_ms: float = DEFAULT_MAX_INTERVAL_MS,
    factor: float = DEFAULT_BACKOFF_FACTOR,
    max_wait_ms: float | None = None,
    on_timeout: Callable[[], Exception] | None = None,
) -> None:
    """Poll ``check`` until it returns true, growing the delay after each attempt.

    ``check`` runs before the first sleep and raises on terminal failure states,
    so callers keep their own error messages.
    """
    start_time = monotonic()
    delay = min(initial_interval_ms, max_interval_ms)

    while True:
        if await check():
            return

        if max_wait_ms is not None and (monotonic() - start_time) * 1000 > max_wait_ms:
            raise on_timeout() if on_timeout else TimeoutError(f"Timeout after {max_wait_ms}ms")

        await asyncio.sleep(delay / 1000)
        delay = min(delay * factor, max_interval_ms)
