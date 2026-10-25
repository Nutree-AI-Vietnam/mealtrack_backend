"""Process-local Redis purge after movement writes (development/local only).

Hosted Redis is purged by the Queue worker that consumes movement events. Docker
Redis has no worker, so the API registers this hook at startup and movement
write handlers await it before they return.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

LocalMovementCacheInvalidationHook = Callable[[str, date], Awaitable[None]]
_local_movement_cache_invalidation_hook: LocalMovementCacheInvalidationHook | None = (
    None
)


def register_local_movement_cache_invalidation_hook(
    hook: LocalMovementCacheInvalidationHook | None,
) -> None:
    """Register the process-local Redis purge used when the Queue worker is absent."""
    global _local_movement_cache_invalidation_hook
    _local_movement_cache_invalidation_hook = hook


async def invoke_local_movement_cache_invalidation_hook(
    user_id: str, log_date: date
) -> None:
    """Invoke the process-local Redis purge when registered (development/local only)."""
    if _local_movement_cache_invalidation_hook is not None:
        await _local_movement_cache_invalidation_hook(user_id, log_date)
