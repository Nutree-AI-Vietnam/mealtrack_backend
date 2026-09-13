"""Purge meal-write Redis keys on the local Docker cache the Worker cannot reach."""

from __future__ import annotations

import logging
from datetime import date, datetime

from src.api.services.local_meal_insight_cache import redis_url_is_local
from src.domain.cache.cache_invalidation_operations import (
    DELETE_KEY,
    DELETE_PATTERN,
    build_meal_invalidation_operations,
)
from src.domain.ports.cache_port import CachePort

logger = logging.getLogger(__name__)


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


async def apply_meal_write_cache_invalidation(
    cache: CachePort | None,
    user_id: str,
    meal_date: date | datetime,
    old_meal_date: date | datetime | None = None,
) -> None:
    """Delete daily-macros keys on Docker Redis so Coach cannot keep a stale 0."""
    if cache is None or not user_id:
        return
    redis_url = getattr(getattr(cache, "redis", None), "_redis_url", "")
    if isinstance(redis_url, str) and redis_url and not redis_url_is_local(redis_url):
        return
    dates = {_as_date(meal_date)}
    if old_meal_date is not None:
        dates.add(_as_date(old_meal_date))
    try:
        for day in dates:
            for operation in build_meal_invalidation_operations(user_id, day):
                if operation.get("op") == DELETE_KEY:
                    await cache.invalidate(operation["key"])
                elif operation.get("op") == DELETE_PATTERN:
                    await cache.invalidate_pattern(operation["pattern"])
    except Exception:
        logger.warning(
            "local meal cache invalidation failed user_id=%s",
            user_id,
            exc_info=True,
        )
