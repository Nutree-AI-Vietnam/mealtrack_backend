"""Purge the Redis keys a meal, hydration or movement write makes stale.

Write handlers await this after commit and before they respond, in every
environment, so the next read sees the write without waiting on the Queue
worker. The builders name exact keys and one revision bump, so the write path
costs two Redis round-trips and never SCANs the keyspace.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from src.domain.cache.cache_invalidation_operations import (
    BUMP_REVISION,
    DELETE_KEY,
    DELETE_PATTERN,
    build_hydration_invalidation_operations,
    build_meal_invalidation_operations,
    build_movement_invalidation_operations,
)
from src.domain.ports.cache_port import CachePort

logger = logging.getLogger(__name__)


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _dedupe(operations: list[dict[str, str]]) -> list[dict[str, str]]:
    """Two days of one week share week keys and the revision bump."""
    unique: dict[tuple[tuple[str, str], ...], dict[str, str]] = {}
    for operation in operations:
        unique.setdefault(tuple(sorted(operation.items())), operation)
    return list(unique.values())


async def _apply_operations(cache: CachePort, operations: list[dict[str, str]]) -> None:
    # Fence range-keyed caches first: it is one INCR and the deletes may fail.
    for operation in operations:
        if operation.get("op") == BUMP_REVISION:
            await cache.bump_revision(operation["key"], int(operation["ttl"]))
    keys = [op["key"] for op in operations if op.get("op") == DELETE_KEY]
    if keys:
        await cache.invalidate_many(keys)
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
