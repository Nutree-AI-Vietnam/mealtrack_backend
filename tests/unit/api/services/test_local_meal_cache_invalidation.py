from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.api.services.local_meal_cache_invalidation import (
    apply_meal_write_cache_invalidation,
)
from src.domain.cache.cache_keys import CacheKeys


@pytest.mark.asyncio
async def test_meal_write_invalidation_deletes_daily_macros_key() -> None:
    cache = AsyncMock()
    user_id = "user-1"
    meal_date = date(2026, 9, 11)

    await apply_meal_write_cache_invalidation(cache, user_id, meal_date)

    cache.invalidate.assert_any_await(CacheKeys.daily_macros(user_id, meal_date)[0])
    assert cache.invalidate_pattern.await_count >= 1


@pytest.mark.asyncio
async def test_meal_write_invalidation_runs_for_docker_redis() -> None:
    cache = AsyncMock()
    cache.redis = type("Redis", (), {"_redis_url": "redis://localhost:6379/0"})()
    user_id = "user-1"
    meal_date = date(2026, 9, 11)

    await apply_meal_write_cache_invalidation(cache, user_id, meal_date)

    cache.invalidate.assert_any_await(CacheKeys.daily_macros(user_id, meal_date)[0])


@pytest.mark.asyncio
async def test_meal_write_invalidation_covers_moved_meal_dates() -> None:
    cache = AsyncMock()
    user_id = "user-1"

    await apply_meal_write_cache_invalidation(
        cache,
        user_id,
        date(2026, 9, 11),
        old_meal_date=date(2026, 9, 10),
    )

    cache.invalidate.assert_any_await(
        CacheKeys.daily_macros(user_id, date(2026, 9, 11))[0]
    )
    cache.invalidate.assert_any_await(
        CacheKeys.daily_macros(user_id, date(2026, 9, 10))[0]
    )


@pytest.mark.asyncio
async def test_meal_write_invalidation_skips_hosted_redis() -> None:
    cache = AsyncMock()
    cache.redis = type(
        "Redis", (), {"_redis_url": "rediss://example.upstash.io:6379"}
    )()

    await apply_meal_write_cache_invalidation(cache, "user-1", date(2026, 9, 11))

    cache.invalidate.assert_not_awaited()
    cache.invalidate_pattern.assert_not_awaited()
