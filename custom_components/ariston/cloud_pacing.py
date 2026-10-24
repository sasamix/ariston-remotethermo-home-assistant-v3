"""Adaptive pacing for Ariston cloud requests.

Poll at the configured interval while the cloud answers normally. When it
signals overload or is unreachable (HTTP 429/5xx, timeouts, connection
errors), back off exponentially and return to the configured interval after
the next successful answer. Authorization failures are not overload.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

MAX_BACKOFF = timedelta(minutes=30)


def is_overload(error: BaseException) -> bool:
    """True for errors that mean "ask less often", not "ask differently"."""
    if isinstance(error, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    status = None
    if getattr(error, "args", None):
        status = error.args[0]
    if status is None:
        status = getattr(error, "status", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    name = type(error).__name__
    # aiohttp client errors without importing aiohttp here.
    return name in {"ClientConnectionError", "ClientConnectorError", "ServerDisconnectedError",
                    "ServerTimeoutError", "ClientOSError"}


def backoff_interval(base: timedelta, failures: int, maximum: timedelta = MAX_BACKOFF) -> timedelta:
    """Base interval doubled per consecutive overload failure, capped."""
    if failures <= 0:
        return base
    return max(base, min(base * (2 ** failures), maximum))


class AdaptivePacer:
    """Decides when a request class is due; no I/O of its own."""

    def __init__(self, base: timedelta, maximum: timedelta = MAX_BACKOFF) -> None:
        self.base = base
        self.maximum = maximum
        self.failures = 0
        self.next_at: datetime | None = None

    @property
    def interval(self) -> timedelta:
        return backoff_interval(self.base, self.failures, self.maximum)

    def due(self, now: datetime) -> bool:
        return self.next_at is None or now >= self.next_at

    def success(self, now: datetime) -> None:
        self.failures = 0
        self.next_at = now + self.base

    def failure(self, now: datetime, error: BaseException | None = None) -> None:
        if error is None or is_overload(error):
            self.failures += 1
        self.next_at = now + self.interval
