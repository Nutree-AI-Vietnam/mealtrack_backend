import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.api.services import write_cache_invalidation
from src.api.services.write_cache_invalidation import (
    apply_hydration_write_cache_invalidation,
    apply_meal_write_cache_invalidation,
    apply_movement_write_cache_invalidation,
)
from src.domain.cache.cache_invalidation_operations import SUPPORTED_LANGUAGES
from src.domain.cache.cache_keys import CacheKeys

HOSTED = type("Redis", (), {"_redis_url": "rediss://example.upstash.io:6379"})()


def _hosted_cache() -> AsyncMock:
    cache = AsyncMock()
    cache.redis = HOSTED
    return cache


def _deleted(cache: AsyncMock) -> set[str]:
    return {key for call in cache.purge.await_args_list for key in call.args[0]}


def _bumped(cache: AsyncMock) -> dict[str, int]:
    return {
        key: ttl
        for call in cache.purge.await_args_list
        for key, ttl in call.args[1].items()
    }


@pytest.mark.asyncio
async def test_meal_write_purges_hosted_redis_in_one_call_without_scanning() -> None:
    """The Queue worker may be down; the write itself must clear what it staled."""
    cache = _hosted_cache()
    user_id = "user-1"
    meal_date = date(2026, 10, 25)  # Sunday
    week_start = date(2026, 10, 19)

    await apply_meal_write_cache_invalidation(cache, user_id, meal_date)

    deleted = _deleted(cache)
    for language in SUPPORTED_LANGUAGES:
        assert CacheKeys.daily_activities(user_id, meal_date, language)[0] in deleted
    assert CacheKeys.daily_macros(user_id, meal_date)[0] in deleted
    assert CacheKeys.weekly_budget(user_id, week_start)[0] in deleted
    assert CacheKeys.weekly_budget(user_id, week_start, meal_date)[0] in deleted
    assert CacheKeys.daily_breakdown(user_id, week_start)[0] in deleted
    assert CacheKeys.user_streak(user_id)[0] in deleted
    # Every delete and the revision bump reach Redis as one round-trip.
    cache.purge.assert_awaited_once()
    assert _bumped(cache) == dict([CacheKeys.intake_revision(user_id)])
    cache.invalidate_pattern.assert_not_awaited()


@pytest.mark.asyncio
async def test_meal_write_covers_moved_meal_dates_in_one_call() -> None:
    cache = _hosted_cache()
    user_id = "user-1"

    await apply_meal_write_cache_invalidation(
        cache,
        user_id,
        date(2026, 9, 11),
        old_meal_date=date(2026, 9, 10),
    )

    deleted = _deleted(cache)
    assert CacheKeys.daily_macros(user_id, date(2026, 9, 11))[0] in deleted
    assert CacheKeys.daily_macros(user_id, date(2026, 9, 10))[0] in deleted
    cache.purge.assert_awaited_once()
    keys = cache.purge.await_args.args[0]
    assert len(keys) == len(set(keys))
    assert _bumped(cache) == dict([CacheKeys.intake_revision(user_id)])


@pytest.mark.asyncio
async def test_meal_write_invalidation_failure_does_not_fail_the_write() -> None:
    cache = _hosted_cache()
    cache.purge.side_effect = ConnectionError("redis down")

    await apply_meal_write_cache_invalidation(cache, "user-1", date(2026, 10, 25))

    cache.purge.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_slow_redis_releases_the_write_but_still_purges(
    monkeypatch,
) -> None:
    """Day macros live a day, so a late purge must finish, not be cancelled."""
    monkeypatch.setattr(write_cache_invalidation, "PURGE_TIMEOUT_SECONDS", 0.01)
    cache = _hosted_cache()
    redis_answers = asyncio.Event()
    purged = asyncio.Event()

    async def slow_purge(*_args) -> None:
        await redis_answers.wait()
        purged.set()

    cache.purge.side_effect = slow_purge

    await asyncio.wait_for(
        apply_meal_write_cache_invalidation(cache, "user-1", date(2026, 10, 25)),
        timeout=1,
    )
    assert not purged.is_set()

    redis_answers.set()
    await asyncio.wait_for(purged.wait(), timeout=1)
    assert CacheKeys.daily_macros("user-1", date(2026, 10, 25))[0] in _deleted(cache)


@pytest.mark.asyncio
async def test_hydration_write_purges_streak_and_hydration_keys() -> None:
    cache = _hosted_cache()
    user_id = "user-1"
    log_date = date(2026, 10, 20)  # Tuesday
    week_start = date(2026, 10, 19)

    await apply_hydration_write_cache_invalidation(cache, user_id, log_date)

    deleted = _deleted(cache)
    assert CacheKeys.user_streak(user_id)[0] in deleted
    for language in SUPPORTED_LANGUAGES:
        assert CacheKeys.daily_hydration(user_id, log_date, language)[0] in deleted
        assert CacheKeys.daily_activities(user_id, log_date, language)[0] in deleted
    assert CacheKeys.weekly_hydration(user_id, week_start)[0] in deleted
    assert CacheKeys.daily_macros(user_id, log_date)[0] in deleted
    assert CacheKeys.daily_breakdown(user_id, week_start)[0] in deleted
    assert CacheKeys.weekly_budget(user_id, week_start, log_date)[0] in deleted
    cache.purge.assert_awaited_once()
    assert _bumped(cache) == dict([CacheKeys.intake_revision(user_id)])
    cache.invalidate_pattern.assert_not_awaited()


@pytest.mark.asyncio
async def test_hydration_write_invalidation_failure_does_not_fail_the_write() -> None:
    cache = _hosted_cache()
    cache.purge.side_effect = ConnectionError("redis down")

    await apply_hydration_write_cache_invalidation(cache, "user-1", date(2026, 10, 20))

    cache.purge.assert_awaited()


@pytest.mark.asyncio
async def test_movement_write_purges_day_keys_but_not_streak() -> None:
    cache = _hosted_cache()
    user_id = "user-1"
    log_date = date(2026, 10, 20)  # Tuesday
    week_start = date(2026, 10, 19)

    await apply_movement_write_cache_invalidation(cache, user_id, log_date)

    deleted = _deleted(cache)
    for language in SUPPORTED_LANGUAGES:
        assert CacheKeys.daily_activities(user_id, log_date, language)[0] in deleted
    assert CacheKeys.daily_macros(user_id, log_date)[0] in deleted
    assert CacheKeys.daily_breakdown(user_id, week_start)[0] in deleted
    assert CacheKeys.weekly_budget(user_id, week_start, log_date)[0] in deleted
    # Movement does not count toward the streak.
    assert CacheKeys.user_streak(user_id)[0] not in deleted
    cache.purge.assert_awaited_once()
    assert _bumped(cache) == dict([CacheKeys.intake_revision(user_id)])
    cache.invalidate_pattern.assert_not_awaited()


@pytest.mark.asyncio
async def test_movement_write_invalidation_failure_does_not_fail_the_write() -> None:
    cache = _hosted_cache()
    cache.purge.side_effect = ConnectionError("redis down")

    await apply_movement_write_cache_invalidation(cache, "user-1", date(2026, 10, 20))

    cache.purge.assert_awaited()
