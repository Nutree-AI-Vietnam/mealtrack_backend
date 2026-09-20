"""Process-local meal-scan concurrency so vision work cannot flood the event loop."""

from __future__ import annotations

import asyncio

_semaphore: asyncio.Semaphore | None = None


def get_scan_semaphore(limit: int) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, limit))
    return _semaphore


def reset_scan_concurrency_for_tests() -> None:
    global _semaphore
    _semaphore = None
