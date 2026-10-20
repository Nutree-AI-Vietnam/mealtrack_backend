"""Purge meal-, hydration- and movement-write Redis keys on the local Docker cache the Worker cannot reach."""

from __future__ import annotations

import logging
from datetime import date, datetime

from src.api.services.local_meal_insight_cache import redis_url_is_local
from src.domain.cache.cache_invalidation_operations import (
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


def _is_hosted_redis(cache: CachePort) -> bool:
    redis_url = getattr(getattr(cache, "redis", None), "_redis_url", "")
    return (
        isinstance(redis_url, str)
        and bool(redis_url)
        and not redis_url_is_local(redis_url)
    )


async def _apply_operations(cache: CachePort, operations: list[dict[str, str]]) -> None:
    for operation in operations:
        if operation.get("op") == DELETE_KEY:
            await cache.invalidate(operation["key"])
        elif operation.get("op") == DELETE_PATTERN:
            await cache.invalidate_pattern(operation["pattern"])


async def apply_meal_write_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    meal_date: date | datetime,
    old_meal_date: date | datetime | None = None,
) -> None:
    """Delete daily-macros keys on Docker Redis so Coach cannot keep a stale 0."""
    if cache is None or not user_id or _is_hosted_redis(cache):
        return
    dates = {_as_date(meal_date)}
    if old_meal_date is not None:
        dates.add(_as_date(old_meal_date))
    try:
        for day in dates:
            await _apply_operations(
                cache, build_meal_invalidation_operations(user_id, day)
            )
    except Exception:
        logger.warning(
            "local meal cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )


async def apply_hydration_write_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    log_date: date | datetime,
) -> None:
    """Delete streak, hydration and day-total keys on Docker Redis after a drink write."""
    if cache is None or not user_id or _is_hosted_redis(cache):
        return
    try:
        await _apply_operations(
            cache,
            build_hydration_invalidation_operations(user_id, _as_date(log_date)),
        )
    except Exception:
        logger.warning(
            "local hydration cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )


async def apply_movement_write_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    log_date: date | datetime,
) -> None:
    """Delete activity, day-total and weekly budget keys on Docker Redis after a movement write."""
    if cache is None or not user_id or _is_hosted_redis(cache):
        return
    try:
        await _apply_operations(
            cache,
            build_movement_invalidation_operations(user_id, _as_date(log_date)),
        )
    except Exception:
        logger.warning(
            "local movement cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )
