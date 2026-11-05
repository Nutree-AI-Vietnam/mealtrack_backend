"""Redis purge every hydration write awaits before it returns.

The API registers this hook at startup in every environment, so a read after
the write never waits on the background handlers of hydration events.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date

LocalHydrationCacheInvalidationHook = Callable[[str, date], Awaitable[None]]
_local_hydration_cache_invalidation_hook: LocalHydrationCacheInvalidationHook | None = (
    None
)


def register_local_hydration_cache_invalidation_hook(
    hook: LocalHydrationCacheInvalidationHook | None,
) -> None:
    """Register the Redis purge hydration writes await before they return."""
    global _local_hydration_cache_invalidation_hook
    _local_hydration_cache_invalidation_hook = hook


async def invoke_local_hydration_cache_invalidation_hook(
    user_id: str, log_date: date
) -> None:
    """Purge the caches this write made stale; a no-op until the API registers it."""
    if _local_hydration_cache_invalidation_hook is not None:
        await _local_hydration_cache_invalidation_hook(user_id, log_date)
