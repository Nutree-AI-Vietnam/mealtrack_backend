"""Tests that get_daily_macros handler opens AsyncUnitOfWork once on cache miss."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.handlers.query_handlers.get_daily_macros_query_handler import (
    GetDailyMacrosQueryHandler,
)
from src.app.queries.meal import GetDailyMacrosQuery
from src.domain.model.weekly.weekly_macro_budget import WeeklyMacroBudget
from src.domain.services.weekly_budget_service import (
    AdjustedDailyTargets,
    EffectiveAdjustedResult,
)


def _make_handler():
    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=None)  # cache miss
    cache.set_json = AsyncMock()
    return GetDailyMacrosQueryHandler(cache_service=cache)


@pytest.mark.asyncio
async def test_cache_miss_opens_uow_once():
    """On a cache miss, AsyncUnitOfWork is instantiated exactly once for DB reads."""
    handler = _make_handler()
    query = GetDailyMacrosQuery(user_id="u1", target_date=date(2026, 4, 18))

    with patch(
        "src.app.handlers.query_handlers.get_daily_macros_query_handler.AsyncUnitOfWork"
    ) as mock_cls:
        mock_uow = AsyncMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)
        fake_user = MagicMock()
        fake_user.timezone = "UTC"
        mock_uow.users.find_by_id = AsyncMock(return_value=fake_user)
        mock_uow.users.get_profile = AsyncMock(return_value=None)
        mock_uow.meals.find_by_date = AsyncMock(return_value=[])
        mock_uow.meals.sum_hydration_ml_for_date = AsyncMock(return_value=0)
        mock_uow.hydration_entries.find_by_date = AsyncMock(return_value=[])
        mock_uow.hydration_entries.sum_ml_for_date = AsyncMock(return_value=0)
        mock_uow.movement_entries.sum_included_kcal_for_range = AsyncMock(
            return_value=0
        )
        mock_uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=None)
        mock_cls.return_value = mock_uow

        # Patch TDEE handler so it doesn't open its own UoW.
        # We patch the definition module (not the handler module) because the
        # handler imports GetUserTdeeQueryHandler lazily inside handle(); Python's
        # module cache ensures the patched class is returned when the deferred
        # import executes.
        with patch(
            "src.app.handlers.query_handlers.get_user_tdee_query_handler.GetUserTdeeQueryHandler"
        ) as mock_tdee_cls:
            mock_tdee = MagicMock()
            mock_tdee.handle = AsyncMock(
                return_value={"target_calories": 2000, "macros": {}, "bmr": 1800}
            )
            mock_tdee_cls.return_value = mock_tdee
            await handler.handle(query)
            mock_tdee_cls.assert_called_once_with(cache_service=handler.cache_service)

    assert (
        mock_cls.call_count == 1
    ), f"Expected 1 UoW open on cache miss, got {mock_cls.call_count}"


@pytest.mark.asyncio
async def test_weekly_budget_fetched_in_shared_uow():
    """weekly_budgets.find_by_user_and_week is called on the same UoW as find_by_date."""
    handler = _make_handler()
    query = GetDailyMacrosQuery(user_id="u1", target_date=date(2026, 4, 18))
    uow_instances = []

    class TrackingUow:
        def __init__(self):
            self.users = AsyncMock()
            fake_user = MagicMock()
            fake_user.timezone = "UTC"
            self.users.find_by_id = AsyncMock(return_value=fake_user)
            self.users.get_profile = AsyncMock(return_value=None)
            self.meals = AsyncMock()
            self.meals.find_by_date = AsyncMock(return_value=[])
            self.meals.sum_hydration_ml_for_date = AsyncMock(return_value=0)
            self.hydration_entries = AsyncMock()
            self.hydration_entries.find_by_date = AsyncMock(return_value=[])
            self.hydration_entries.sum_ml_for_date = AsyncMock(return_value=0)
            self.movement_entries = AsyncMock()
            self.movement_entries.sum_included_kcal_for_range = AsyncMock(
                return_value=0
            )
            self.weekly_budgets = AsyncMock()
            self.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=None)
            uow_instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    with patch(
        "src.app.handlers.query_handlers.get_daily_macros_query_handler.AsyncUnitOfWork",
        TrackingUow,
    ):
        # Same patch strategy: target definition module for deferred import.
        with patch(
            "src.app.handlers.query_handlers.get_user_tdee_query_handler.GetUserTdeeQueryHandler"
        ) as mock_tdee_cls:
            mock_tdee = MagicMock()
            mock_tdee.handle = AsyncMock(
                return_value={"target_calories": 2000, "macros": {}, "bmr": 1800}
            )
            mock_tdee_cls.return_value = mock_tdee
            await handler.handle(query)
            mock_tdee_cls.assert_called_once_with(cache_service=handler.cache_service)

    first_uow = uow_instances[0]
    first_uow.meals.find_by_date.assert_awaited_once()
    first_uow.weekly_budgets.find_by_user_and_week.assert_awaited_once()


@pytest.mark.asyncio
async def test_weekly_budget_present_matching_revision_locks_weekly_context():
    """CHARACTERIZATION: weekly_budget present + target_revision matching the
    TDEE profile_target_revision routes through
    WeeklyBudgetService.get_effective_adjusted_daily_async and locks the
    `weekly_context` dict shape for a fixed fixture.

    Wave 2 (item 16): also locks the POST-consolidation single-UoW behavior —
    `handle()` opens exactly one AsyncUnitOfWork, resolves TDEE before
    opening it, and `_get_weekly_context` reuses that same UoW instead of
    opening a second one.
    """
    handler = _make_handler()
    week_start = date(2026, 4, 20)  # Monday, so get_user_monday(target_date) == target_date
    target_date = week_start
    query = GetDailyMacrosQuery(user_id="u1", target_date=target_date)

    weekly_budget = WeeklyMacroBudget(
        weekly_budget_id="budget-1",
        user_id="u1",
        week_start_date=week_start,
        target_calories=14000.0,
        target_protein=700.0,
        target_carbs=1750.0,
        target_fat=466.6667,
        target_revision=3,
    )

    uow_instances = []

    class TrackingUow:
        def __init__(self):
            self.users = AsyncMock()
            fake_user = MagicMock()
            fake_user.timezone = "UTC"
            self.users.find_by_id = AsyncMock(return_value=fake_user)
            self.users.get_profile = AsyncMock(return_value=None)
            self.meals = AsyncMock()
            self.meals.find_by_date = AsyncMock(return_value=[])
            self.meals.sum_hydration_ml_for_date = AsyncMock(return_value=0)
            self.hydration_entries = AsyncMock()
            self.hydration_entries.find_by_date = AsyncMock(return_value=[])
            self.hydration_entries.sum_ml_for_date = AsyncMock(return_value=0)
            self.movement_entries = AsyncMock()
            self.movement_entries.sum_included_kcal_for_range = AsyncMock(
                return_value=0
            )
            self.weekly_budgets = AsyncMock()
            self.weekly_budgets.find_by_user_and_week = AsyncMock(
                return_value=weekly_budget
            )
            uow_instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    effective = EffectiveAdjustedResult(
        adjusted=AdjustedDailyTargets(
            calories=2000.0,
            carbs=250.0,
            fat=66.7,
            protein=100.0,
            bmr_floor_active=False,
            remaining_days=7,
        ),
        consumed_before_today={
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
        },
        consumed_total={
            "calories": 2100.0,
            "protein": 100.0,
            "carbs": 250.0,
            "fat": 100.0,
        },
        logged_past_days=0,
        skipped_days=0,
        show_logging_prompt=False,
    )

    with (
        patch(
            "src.app.handlers.query_handlers.get_daily_macros_query_handler.AsyncUnitOfWork",
            TrackingUow,
        ),
        patch(
            "src.app.handlers.query_handlers.get_user_tdee_query_handler.GetUserTdeeQueryHandler"
        ) as mock_tdee_cls,
        patch(
            "src.app.handlers.query_handlers.get_daily_macros_query_handler."
            "WeeklyBudgetService.get_effective_adjusted_daily_async",
            new_callable=AsyncMock,
        ) as mock_get_effective,
    ):
        mock_tdee = MagicMock()
        mock_tdee.handle = AsyncMock(
            return_value={
                "target_calories": 2000,
                "macros": {"protein": 150.0, "carbs": 200.0, "fat": 65.0},
                "bmr": 1800,
                "profile_target_revision": 3,
                "macro_preset": "standard",
                "is_custom": False,
            }
        )
        mock_tdee_cls.return_value = mock_tdee
        mock_get_effective.return_value = effective

        result = await handler.handle(query)

    # Post-item-16 behavior: exactly one AsyncUnitOfWork instance is opened;
    # _get_weekly_context reuses it instead of opening a second one.
    assert len(uow_instances) == 1, (
        f"Expected 1 UoW open (consolidated single-UoW), got {len(uow_instances)}"
    )

    mock_get_effective.assert_awaited_once()
    call_kwargs = mock_get_effective.await_args.kwargs
    assert call_kwargs["uow"] is uow_instances[0]
    assert call_kwargs["user_id"] == "u1"
    assert call_kwargs["week_start"] == week_start
    assert call_kwargs["weekly_budget"] is weekly_budget
    assert call_kwargs["base_daily_cal"] == 2000
    assert call_kwargs["base_daily_protein"] == 150.0
    assert call_kwargs["base_daily_carbs"] == 200.0
    assert call_kwargs["base_daily_fat"] == 65.0
    assert call_kwargs["bmr"] == 1800

    # Locked weekly_context shape for this fixed fixture (STANDARD preset,
    # not custom → apply_adjusted_macro_policy passes adjusted values through
    # unchanged).
    assert result["weekly_context"] == {
        "adjusted_target_calories": 2000.0,
        "adjusted_target_carbs": 250.0,
        "adjusted_target_fat": 66.7,
        "daily_protein": 100.0,
        "bmr_floor_active": False,
        "remaining_days": 7,
    }


@pytest.mark.asyncio
async def test_stale_target_revision_skips_weekly_context():
    """CHARACTERIZATION: weekly_budget present but target_revision MISMATCHES
    the TDEE profile_target_revision → _get_weekly_context refuses the stale
    row, returns None, and get_effective_adjusted_daily_async is never called.
    """
    handler = _make_handler()
    week_start = date(2026, 4, 20)
    target_date = week_start
    query = GetDailyMacrosQuery(user_id="u1", target_date=target_date)

    weekly_budget = WeeklyMacroBudget(
        weekly_budget_id="budget-1",
        user_id="u1",
        week_start_date=week_start,
        target_calories=14000.0,
        target_protein=700.0,
        target_carbs=1750.0,
        target_fat=466.6667,
        target_revision=1,  # mismatches TDEE's profile_target_revision=3 below
    )

    mock_uow = AsyncMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    fake_user = MagicMock()
    fake_user.timezone = "UTC"
    mock_uow.users.find_by_id = AsyncMock(return_value=fake_user)
    mock_uow.users.get_profile = AsyncMock(return_value=None)
    mock_uow.meals.find_by_date = AsyncMock(return_value=[])
    mock_uow.meals.sum_hydration_ml_for_date = AsyncMock(return_value=0)
    mock_uow.hydration_entries.find_by_date = AsyncMock(return_value=[])
    mock_uow.hydration_entries.sum_ml_for_date = AsyncMock(return_value=0)
    mock_uow.movement_entries.sum_included_kcal_for_range = AsyncMock(return_value=0)
    mock_uow.weekly_budgets.find_by_user_and_week = AsyncMock(
        return_value=weekly_budget
    )

    with (
        patch(
            "src.app.handlers.query_handlers.get_daily_macros_query_handler.AsyncUnitOfWork",
            return_value=mock_uow,
        ),
        patch(
            "src.app.handlers.query_handlers.get_user_tdee_query_handler.GetUserTdeeQueryHandler"
        ) as mock_tdee_cls,
        patch(
            "src.app.handlers.query_handlers.get_daily_macros_query_handler."
            "WeeklyBudgetService.get_effective_adjusted_daily_async",
            new_callable=AsyncMock,
        ) as mock_get_effective,
    ):
        mock_tdee = MagicMock()
        mock_tdee.handle = AsyncMock(
            return_value={
                "target_calories": 2000,
                "macros": {"protein": 150.0, "carbs": 200.0, "fat": 65.0},
                "bmr": 1800,
                "profile_target_revision": 3,
                "macro_preset": "standard",
                "is_custom": False,
            }
        )
        mock_tdee_cls.return_value = mock_tdee

        result = await handler.handle(query)

    mock_get_effective.assert_not_awaited()
    assert "weekly_context" not in result


@pytest.mark.asyncio
async def test_cache_read_failure_is_non_fatal():
    handler = _make_handler()
    handler.cache_service.get_json = AsyncMock(
        side_effect=RuntimeError("attached to a different loop")
    )

    result = await handler._try_get_cached_result("u1", date(2026, 4, 18), 1, True)

    assert result is None


@pytest.mark.asyncio
async def test_stale_daily_target_cache_is_rejected():
    handler = _make_handler()
    handler.cache_service.get_json = AsyncMock(return_value={"target_revision": 1})

    assert await handler._try_get_cached_result("u1", date(2026, 4, 18), 2, True) is None


@pytest.mark.asyncio
async def test_cache_write_failure_is_non_fatal():
    handler = _make_handler()
    handler.cache_service.set_json = AsyncMock(
        side_effect=RuntimeError("attached to a different loop")
    )

    await handler._write_cache("u1", date(2026, 4, 18), {"ok": True})
