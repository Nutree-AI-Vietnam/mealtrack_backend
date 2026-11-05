"""Purge the Redis keys a meal, hydration or movement write makes stale.

Write handlers await this after commit and before they respond, in every
environment, so the next read sees the write without waiting on the Queue
worker. The builders name exact keys and one revision bump, sent as one
pipelined Redis round-trip; the write path never SCANs the keyspace.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from src.domain.cache.cache_invalidation_operations import (
    BUMP_REVISION,
    DELETE_KEY,
    DELETE_PATTERN,
    build_activity_calories_invalidation_operations,
    build_cheat_day_invalidation_operations,
    build_hydration_invalidation_operations,
    build_meal_invalidation_operations,
    build_movement_invalidation_operations,
    build_profile_invalidation_operations,
    build_saved_suggestion_invalidation_operations,
    build_supplement_invalidation_operations,
)
from src.domain.ports.cache_port import CachePort

logger = logging.getLogger(__name__)

# A healthy purge is one round-trip (~10 ms on Upstash). The deadline keeps a
# slow Redis from adding its 5 s socket timeout to every write. A purge past
# the deadline is not cancelled: day macros live a day, so it finishes in the
# background and clears them once Redis answers.
PURGE_TIMEOUT_SECONDS = 1.0

# Strong references so a purge the write stopped waiting for is not collected.
_purges: set[asyncio.Task[None]] = set()


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _dedupe(operations: list[dict[str, str]]) -> list[dict[str, str]]:
    """Two days of one week share week keys and the revision bump."""
    unique: dict[tuple[tuple[str, str], ...], dict[str, str]] = {}
    for operation in operations:
        unique.setdefault(tuple(sorted(operation.items())), operation)
    return list(unique.values())


async def _apply_operations(cache: CachePort, operations: list[dict[str, str]]) -> None:
    purge = asyncio.ensure_future(_purge(cache, operations))
    _purges.add(purge)
    purge.add_done_callback(_purges.discard)
    try:
        await asyncio.wait_for(asyncio.shield(purge), timeout=PURGE_TIMEOUT_SECONDS)
    except TimeoutError:
        purge.add_done_callback(_log_late_purge_failure)
        logger.warning(
            "write cache purge passed %.1fs; finishing in the background",
            PURGE_TIMEOUT_SECONDS,
        )


def _log_late_purge_failure(purge: asyncio.Task[None]) -> None:
    if not purge.cancelled() and purge.exception() is not None:
        logger.warning(
            "background write cache purge failed", exc_info=purge.exception()
        )


async def _purge(cache: CachePort, operations: list[dict[str, str]]) -> None:
    await cache.purge(
        [op["key"] for op in operations if op.get("op") == DELETE_KEY],
        {
            op["key"]: int(op["ttl"])
            for op in operations
            if op.get("op") == BUMP_REVISION
        },
    )
    for operation in operations:
        if operation.get("op") == DELETE_PATTERN:
            await cache.invalidate_pattern(operation["pattern"])


async def apply_meal_write_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    meal_date: date | datetime,
    old_meal_date: date | datetime | None = None,
) -> None:
    """Purge the meal's day (and the day it moved from) before the write returns."""
    if cache is None or not user_id:
        return
    dates = {_as_date(meal_date)}
    if old_meal_date is not None:
        dates.add(_as_date(old_meal_date))
    operations = [
        operation
        for day in sorted(dates)
        for operation in build_meal_invalidation_operations(user_id, day)
    ]
    try:
        await _apply_operations(cache, _dedupe(operations))
    except Exception:
        logger.warning(
            "meal write cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )


async def apply_hydration_write_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    log_date: date | datetime,
) -> None:
    """Purge streak, hydration and day-total keys before a drink write returns."""
    if cache is None or not user_id:
        return
    try:
        await _apply_operations(
            cache,
            build_hydration_invalidation_operations(user_id, _as_date(log_date)),
        )
    except Exception:
        logger.warning(
            "hydration write cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )


async def apply_movement_write_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    log_date: date | datetime,
) -> None:
    """Purge activity, day-total and weekly budget keys before a movement write returns."""
    if cache is None or not user_id:
        return
    try:
        await _apply_operations(
            cache,
            build_movement_invalidation_operations(user_id, _as_date(log_date)),
        )
    except Exception:
        logger.warning(
            "movement write cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )


async def apply_activity_calories_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    today: date,
) -> None:
    """Purge this week's budget keys and fence range caches after the switch."""
    if cache is None or not user_id:
        return
    try:
        await _apply_operations(
            cache, build_activity_calories_invalidation_operations(user_id, today)
        )
    except Exception:
        logger.warning(
            "activity calories cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )


async def apply_supplement_write_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    log_date: date | datetime,
) -> None:
    """Purge activity, day-total and streak keys before a dose write returns."""
    if cache is None or not user_id:
        return
    try:
        await _apply_operations(
            cache,
            build_supplement_invalidation_operations(user_id, _as_date(log_date)),
        )
    except Exception:
        logger.warning(
            "supplement write cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )


# Profile, cheat-day and saved-suggestion writes are purged from their
# integration events by the in-process router, after the write has returned:
# their builders include pattern deletes, which never run on the write path.


async def apply_profile_cache_invalidation(
    cache: CachePort | None, user_id: str
) -> None:
    """Purge the user's targets, totals and budget keys after a profile write."""
    if cache is None or not user_id:
        return
    try:
        await _apply_operations(cache, build_profile_invalidation_operations(user_id))
    except Exception:
        logger.warning(
            "profile cache invalidation failed user_id=%s", user_id, exc_info=True
        )


async def apply_cheat_day_cache_invalidation(
    cache: CachePort | None, user_id: str, cheat_day: date | datetime
) -> None:
    """Purge the cheat day's weekly budget and progress keys."""
    if cache is None or not user_id:
        return
    try:
        await _apply_operations(
            cache,
            build_cheat_day_invalidation_operations(user_id, _as_date(cheat_day)),
        )
    except Exception:
        logger.warning(
            "cheat day cache invalidation failed user_id=%s", user_id, exc_info=True
        )


async def apply_saved_suggestion_cache_invalidation(
    cache: CachePort | None, user_id: str
) -> None:
    """Purge the user's saved suggestions list."""
    if cache is None or not user_id:
        return
    try:
        await _apply_operations(
            cache, build_saved_suggestion_invalidation_operations(user_id)
        )
    except Exception:
        logger.warning(
            "saved suggestion cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )
