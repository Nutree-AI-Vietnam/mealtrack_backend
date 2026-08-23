"""Unit tests for CacheInvalidationService.

Tests verify that each public method invalidates the correct Redis keys
synchronously, retries once on transient failure, and handles the
backdated-entry case by purging both the target week and the current week.
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest

from src.app.services.cache_invalidation_service import CacheInvalidationService
from src.domain.cache.cache_keys import CacheKeys

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_mock():
    mock = AsyncMock()
    mock.invalidate = AsyncMock(return_value=True)
    mock.invalidate_pattern = AsyncMock(return_value=1)
    return mock


@pytest.fixture
def service(cache_mock):
    return CacheInvalidationService(cache_mock)


# ---------------------------------------------------------------------------
# after_meal_write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_meal_write_invalidates_activities_pattern(service, cache_mock):
    """activities pattern is cleared so all language variants are purged."""
    await service.after_meal_write("user1", date(2026, 6, 2))
    cache_mock.invalidate_pattern.assert_any_call("user:user1:activities:2026-06-02:*")


@pytest.mark.asyncio
async def test_after_meal_write_invalidates_daily_macros(service, cache_mock):
    daily_key = CacheKeys.daily_macros("user1", date(2026, 6, 2))[0]
    await service.after_meal_write("user1", date(2026, 6, 2))
    cache_mock.invalidate.assert_any_call(daily_key)


@pytest.mark.asyncio
async def test_after_meal_write_invalidates_weekly_budget_and_breakdown(service, cache_mock):
    # June 2 2026 is a Tuesday; week_start is June 1
    week_start = date(2026, 6, 1)
    weekly_key = CacheKeys.weekly_budget("user1", week_start)[0]
    weekly_pattern = CacheKeys.weekly_budget_pattern("user1", week_start)
    breakdown_key = CacheKeys.daily_breakdown("user1", week_start)[0]
    await service.after_meal_write("user1", date(2026, 6, 2))
    cache_mock.invalidate.assert_any_call(weekly_key)
    cache_mock.invalidate_pattern.assert_any_call(weekly_pattern)
    cache_mock.invalidate.assert_any_call(breakdown_key)


@pytest.mark.asyncio
async def test_after_meal_write_invalidates_streak(service, cache_mock):
    streak_key = CacheKeys.user_streak("user1")[0]
    await service.after_meal_write("user1", date(2026, 6, 2))
    cache_mock.invalidate.assert_any_call(streak_key)


@pytest.mark.asyncio
async def test_after_meal_write_backdated_also_invalidates_current_week(service, cache_mock):
    """A meal logged for a past week must also purge the current week's budget."""
    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())
    past_week_start = current_week_start - timedelta(days=14)
    past_date = past_week_start

    await service.after_meal_write("user1", past_date)

    past_budget_key = CacheKeys.weekly_budget("user1", past_week_start)[0]
    current_budget_key = CacheKeys.weekly_budget("user1", current_week_start)[0]
    cache_mock.invalidate.assert_any_call(past_budget_key)
    cache_mock.invalidate.assert_any_call(current_budget_key)


@pytest.mark.asyncio
async def test_after_meal_write_retries_on_invalidate_pattern_failure(cache_mock):
    """Transient failure on the first pattern triggers one retry on the same key."""
    patterns_called = []

    def flaky(pattern):
        patterns_called.append(pattern)
        if len(patterns_called) == 1:
            raise ConnectionError("redis down")
        return 1

    cache_mock.invalidate_pattern.side_effect = flaky
    svc = CacheInvalidationService(cache_mock)
    # Should not raise — the transient failure is retried, then succeeds.
    await svc.after_meal_write("user1", date(2026, 6, 2))
    # The failed key was retried (attempted twice in a row) before moving on.
    assert patterns_called[0] == patterns_called[1]


@pytest.mark.asyncio
async def test_after_meal_write_retries_on_invalidate_key_failure(cache_mock):
    """Transient failure on first key invalidate triggers one retry."""
    cache_mock.invalidate.side_effect = [ConnectionError("redis down"), None] + [None] * 20
    svc = CacheInvalidationService(cache_mock)
    await svc.after_meal_write("user1", date(2026, 6, 2))
    # First invalidate call retried once → at least 2 calls
    assert cache_mock.invalidate.call_count >= 2


# ---------------------------------------------------------------------------
# after_movement_write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_movement_write_invalidates_activities_pattern(service, cache_mock):
    await service.after_movement_write("user2", date(2026, 6, 2))
    cache_mock.invalidate_pattern.assert_any_call("user:user2:activities:2026-06-02:*")


@pytest.mark.asyncio
async def test_after_movement_write_invalidates_daily_macros_and_weekly_budget(service, cache_mock):
    log_date = date(2026, 6, 2)
    week_start = date(2026, 6, 1)
    daily_key = CacheKeys.daily_macros("user2", log_date)[0]
    weekly_key = CacheKeys.weekly_budget("user2", week_start)[0]
    weekly_pattern = CacheKeys.weekly_budget_pattern("user2", week_start)
    breakdown_key = CacheKeys.daily_breakdown("user2", week_start)[0]

    await service.after_movement_write("user2", log_date)

    cache_mock.invalidate.assert_any_call(daily_key)
    cache_mock.invalidate.assert_any_call(weekly_key)
    cache_mock.invalidate_pattern.assert_any_call(weekly_pattern)
    cache_mock.invalidate.assert_any_call(breakdown_key)


@pytest.mark.asyncio
async def test_after_movement_write_backdated_invalidates_current_week(service, cache_mock):
    today = date.today()
    current_week_start = today - timedelta(days=today.weekday())
    past_date = current_week_start - timedelta(days=14)
    current_budget_key = CacheKeys.weekly_budget("user2", current_week_start)[0]

    await service.after_movement_write("user2", past_date)
    cache_mock.invalidate.assert_any_call(current_budget_key)


# ---------------------------------------------------------------------------
# after_hydration_write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_hydration_write_invalidates_activities_and_hydration_patterns(service, cache_mock):
    await service.after_hydration_write("user3", date(2026, 6, 2))
    cache_mock.invalidate_pattern.assert_any_call("user:user3:activities:2026-06-02:*")
    cache_mock.invalidate_pattern.assert_any_call("user:user3:hydration:2026-06-02:*")


@pytest.mark.asyncio
async def test_after_hydration_write_invalidates_weekly_hydration(service, cache_mock):
    log_date = date(2026, 6, 2)
    week_start = date(2026, 6, 1)
    hydration_weekly_key = CacheKeys.weekly_hydration("user3", week_start)[0]

    await service.after_hydration_write("user3", log_date)
    cache_mock.invalidate.assert_any_call(hydration_weekly_key)


@pytest.mark.asyncio
async def test_after_hydration_write_also_purges_meal_related_keys(service, cache_mock):
    """Caloric drinks affect energy balance — meal-related keys must also be purged."""
    log_date = date(2026, 6, 2)
    week_start = date(2026, 6, 1)
    budget_key = CacheKeys.weekly_budget("user3", week_start)[0]
    budget_pattern = CacheKeys.weekly_budget_pattern("user3", week_start)
    breakdown_key = CacheKeys.daily_breakdown("user3", week_start)[0]
    streak_key = CacheKeys.user_streak("user3")[0]

    await service.after_hydration_write("user3", log_date)

    cache_mock.invalidate.assert_any_call(budget_key)
    cache_mock.invalidate_pattern.assert_any_call(budget_pattern)
    cache_mock.invalidate.assert_any_call(breakdown_key)
    cache_mock.invalidate.assert_any_call(streak_key)


# ---------------------------------------------------------------------------
# after_custom_macros_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_custom_macros_update_purges_macros_pattern(service, cache_mock):
    """All daily_macros entries for the user must be purged via pattern."""
    await service.after_custom_macros_update("user4")
    cache_mock.invalidate_pattern.assert_any_call("user:user4:macros:*")


@pytest.mark.asyncio
async def test_after_custom_macros_update_invalidates_tdee_and_profile(service, cache_mock):
    tdee_key = CacheKeys.user_tdee("user4")[0]
    profile_key = CacheKeys.user_profile("user4")[0]

    await service.after_custom_macros_update("user4")

    cache_mock.invalidate.assert_any_call(tdee_key)
    cache_mock.invalidate.assert_any_call(profile_key)


@pytest.mark.asyncio
async def test_after_custom_macros_update_invalidates_current_and_next_week_budget(service, cache_mock):
    """Weekly budget for this week AND next week must be purged (covers timezone skew)."""
    from datetime import timedelta

    today = date.today()
    this_week = today - timedelta(days=today.weekday())
    next_week = this_week + timedelta(days=7)

    this_budget_key = CacheKeys.weekly_budget("user4", this_week)[0]
    next_budget_key = CacheKeys.weekly_budget("user4", next_week)[0]

    await service.after_custom_macros_update("user4")

    cache_mock.invalidate.assert_any_call(this_budget_key)
    cache_mock.invalidate.assert_any_call(next_budget_key)


# ---------------------------------------------------------------------------
# nutrition_bulk purge (cached date ranges covering any changed date)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_meal_write_purges_nutrition_bulk(service, cache_mock):
    await service.after_meal_write("user1", date(2026, 6, 2))
    cache_mock.invalidate_pattern.assert_any_call("user:user1:nutrition_bulk:*")


@pytest.mark.asyncio
async def test_movement_write_purges_nutrition_bulk(service, cache_mock):
    await service.after_movement_write("user2", date(2026, 6, 2))
    cache_mock.invalidate_pattern.assert_any_call("user:user2:nutrition_bulk:*")


@pytest.mark.asyncio
async def test_hydration_write_purges_nutrition_bulk(service, cache_mock):
    await service.after_hydration_write("user3", date(2026, 6, 2))
    cache_mock.invalidate_pattern.assert_any_call("user:user3:nutrition_bulk:*")


@pytest.mark.asyncio
async def test_custom_macros_update_purges_nutrition_bulk(service, cache_mock):
    await service.after_custom_macros_update("user4")
    cache_mock.invalidate_pattern.assert_any_call("user:user4:nutrition_bulk:*")


# ---------------------------------------------------------------------------
# No-op when cache is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_noop_when_cache_is_none():
    """Service must not raise when constructed with no cache (cache=None)."""
    svc = CacheInvalidationService(None)
    # All methods should return cleanly
    await svc.after_meal_write("u", date(2026, 6, 2))
    await svc.after_movement_write("u", date(2026, 6, 2))
    await svc.after_hydration_write("u", date(2026, 6, 2))
    await svc.after_custom_macros_update("u")


class _FakeTaskManager:
    def __init__(self):
        self.spawned: list[tuple[str, object]] = []

    def spawn(self, name, coro):
        self.spawned.append((name, coro))
        return None


@pytest.mark.asyncio
async def test_schedule_after_movement_write_awaits_without_task_manager(cache_mock):
    svc = CacheInvalidationService(cache_mock)
    await svc.schedule_after_movement_write("user2", date(2026, 6, 2))
    cache_mock.invalidate_pattern.assert_any_call("user:user2:activities:2026-06-02:*")


@pytest.mark.asyncio
async def test_schedule_after_movement_write_spawns_when_task_manager_present(cache_mock):
    tasks = _FakeTaskManager()
    svc = CacheInvalidationService(cache_mock, task_manager=tasks)

    await svc.schedule_after_movement_write("user2", date(2026, 6, 2))

    assert cache_mock.invalidate_pattern.call_count == 0
    assert cache_mock.invalidate.call_count == 0
    assert len(tasks.spawned) == 1
    assert tasks.spawned[0][0].startswith("cache:after_movement_write:user2:")

    await tasks.spawned[0][1]
    cache_mock.invalidate_pattern.assert_any_call("user:user2:activities:2026-06-02:*")
