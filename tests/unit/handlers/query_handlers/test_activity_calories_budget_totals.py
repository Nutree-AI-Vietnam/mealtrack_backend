"""Daily, bulk and weekly totals net logged movement only while activity
calories are on; the burn itself is still reported either way."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.app.handlers.query_handlers.get_daily_macros_query_handler import (
    GetDailyMacrosQueryHandler,
)
from src.app.handlers.query_handlers.get_nutrition_bulk_query_handler import (
    GetNutritionBulkQueryHandler,
)
from src.app.handlers.query_handlers.get_weekly_budget_query_handler import (
    GetWeeklyBudgetQueryHandler,
)
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.app.queries.meal import GetDailyMacrosQuery
from src.app.queries.nutrition import GetNutritionBulkQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import Nutrition
from src.domain.model.user import MacroPreset
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.services.weekly_budget_service import (
    AdjustedDailyTargets,
    EffectiveAdjustedResult,
)

_DAILY = "src.app.handlers.query_handlers.get_daily_macros_query_handler"
_BULK = "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler"
_WEEKLY = "src.app.handlers.query_handlers.get_weekly_budget_query_handler"
_TDEE = "src.app.handlers.query_handlers.get_user_tdee_query_handler"

_DAY = date(2026, 8, 11)
# 100 g protein, 250 g carbs, 100 g fat: 2300 kcal of food.
_FOOD_KCAL = 2300.0
_BURN_KCAL = 200.0
_ON_OFF = pytest.mark.parametrize(
    ("activity_calories", "consumed"),
    [(True, _FOOD_KCAL - _BURN_KCAL), (False, _FOOD_KCAL)],
)


def _meal(created_at: datetime) -> Meal:
    return Meal(
        meal_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        status=MealStatus.READY,
        created_at=created_at,
        image=None,
        dish_name="rice bowl",
        nutrition=Nutrition(macros=Macros(protein=100, carbs=250, fat=100, fiber=0)),
        meal_type="lunch",
        source="manual",
        ready_at=created_at,
    )


def _effective(consumed_kcal: float) -> EffectiveAdjustedResult:
    consumed = {"calories": consumed_kcal, "protein": 100, "carbs": 250, "fat": 100}
    return EffectiveAdjustedResult(
        adjusted=AdjustedDailyTargets(2000.0, 250.0, 66.7, 100.0, False, 6),
        consumed_before_today=consumed,
        consumed_total=consumed,
        logged_past_days=1,
        skipped_days=0,
        show_logging_prompt=False,
    )


def _async_uow() -> AsyncMock:
    uow = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    return uow


# --- Daily macros (Home) ---------------------------------------------------


def _daily_uow(activity_calories: bool) -> AsyncMock:
    uow = _async_uow()
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)
    uow.users.get_activity_calories = AsyncMock(return_value=activity_calories)
    uow.users.get_profile = AsyncMock(return_value=None)
    uow.meals.find_by_date = AsyncMock(
        return_value=[_meal(datetime(2026, 8, 11, 12, tzinfo=UTC))]
    )
    uow.meals.sum_hydration_ml_for_date = AsyncMock(return_value=0)
    uow.hydration_entries.find_by_date = AsyncMock(return_value=[])
    uow.hydration_entries.sum_ml_for_date = AsyncMock(return_value=0)
    uow.movement_entries.sum_included_kcal_for_range = AsyncMock(
        return_value=_BURN_KCAL
    )
    uow.weekly_budgets.find_by_user_and_week = AsyncMock(
        return_value=WeeklyMacroBudget(
            weekly_budget_id="budget-1",
            user_id="u1",
            week_start_date=date(2026, 8, 10),
            target_calories=14000.0,
            target_protein=700.0,
            target_carbs=1750.0,
            target_fat=466.7,
            target_revision=3,
        )
    )
    return uow


async def _daily(uow: AsyncMock, cache=None, effective=None):
    handler = GetDailyMacrosQueryHandler(cache_service=cache)
    tdee = MagicMock()
    tdee.handle = AsyncMock(
        return_value={
            "target_calories": 2000,
            "macros": {"protein": 100, "carbs": 250, "fat": 66.7},
            "bmr": 1600,
            "profile_target_revision": 3,
        }
    )
    get_effective = AsyncMock(return_value=effective or _effective(_FOOD_KCAL))
    with (
        patch(f"{_DAILY}.AsyncUnitOfWork", return_value=uow),
        patch(f"{_TDEE}.GetUserTdeeQueryHandler", return_value=tdee),
        patch(
            f"{_DAILY}.WeeklyBudgetService.get_effective_adjusted_daily_async",
            get_effective,
        ),
    ):
        result = await handler.handle(
            GetDailyMacrosQuery(user_id="u1", target_date=_DAY)
        )
    return result, get_effective


@pytest.mark.asyncio
@_ON_OFF
async def test_daily_totals_net_movement_only_while_activity_calories_on(
    activity_calories, consumed
):
    result, get_effective = await _daily(_daily_uow(activity_calories))

    assert result["total_calories"] == consumed
    assert result["food_calories"] == _FOOD_KCAL
    # The burn is reported either way; only whether it counts changes.
    assert result["movement_kcal_burned"] == _BURN_KCAL
    assert result["activity_calories"] is activity_calories
    # The day's weekly context is built under the same setting.
    assert get_effective.await_args.kwargs["activity_calories"] is activity_calories


@pytest.mark.asyncio
async def test_daily_cache_built_with_activity_calories_on_is_not_served_when_off():
    cache = MagicMock()
    cache.get_json = AsyncMock(
        return_value={
            "total_calories": _FOOD_KCAL - _BURN_KCAL,
            "food_calories": _FOOD_KCAL,
            "target_revision": 3,
            "weekly_auto_adjust": True,
            # Written before the setting existed, so it counted movement.
            "intake_watermark": "||",
        }
    )
    cache.set_json = AsyncMock()
    uow = _daily_uow(activity_calories=False)

    result, _ = await _daily(uow, cache=cache)

    uow.meals.find_by_date.assert_awaited_once()
    assert result["total_calories"] == _FOOD_KCAL
    stored = cache.set_json.await_args.args[1]
    assert stored["activity_calories"] is False


# --- Bulk nutrition (date-indexed day summaries) ---------------------------


@pytest.mark.asyncio
@_ON_OFF
async def test_bulk_day_and_week_net_movement_only_while_activity_calories_on(
    activity_calories, consumed
):
    day = date(2026, 4, 1)
    uow = _async_uow()
    uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)
    uow.users.get_activity_calories = AsyncMock(return_value=activity_calories)
    uow.meals.find_by_date_range = AsyncMock(
        return_value=[_meal(datetime(2026, 4, 1, 12, tzinfo=UTC))]
    )
    uow.hydration_entries.find_by_date_range = AsyncMock(return_value=[])
    uow.movement_entries.fetch_included_kcal_for_range = AsyncMock(
        return_value=[(datetime(2026, 4, 1, 18, tzinfo=UTC), _BURN_KCAL)]
    )
    uow.weekly_budgets.find_by_user_and_week = AsyncMock(
        return_value=MagicMock(target_revision=1, target_calories=14000.0)
    )
    get_effective = AsyncMock(return_value=_effective(consumed))
    handler = GetNutritionBulkQueryHandler()
    handler._get_user_targets = AsyncMock(
        return_value=(
            2000.0,
            {"protein": 100, "carbs": 250, "fat": 66.7},
            1600.0,
            1,
            MacroPreset.STANDARD,
            False,
        )
    )

    with (
        patch(f"{_BULK}.AsyncUnitOfWork", return_value=uow),
        patch(f"{_BULK}.resolve_user_timezone_async", AsyncMock(return_value="UTC")),
        patch(f"{_BULK}.get_user_monday", return_value=date(2026, 3, 30)),
        patch(
            f"{_BULK}.WeeklyBudgetService.get_effective_adjusted_daily_async",
            get_effective,
        ),
    ):
        result = await handler._compute(
            GetNutritionBulkQuery(user_id="u1", start_date=day, end_date=day)
        )

    summary = result["dates"]["2026-04-01"]
    assert summary["food_calories"] == _FOOD_KCAL
    assert summary["movement_kcal_burned"] == _BURN_KCAL
    assert summary["totals"]["consumed"]["calories"] == consumed
    assert summary["totals"]["remaining"]["calories"] == 2000.0 - consumed
    assert result["activity_calories"] is activity_calories
    assert get_effective.await_args.kwargs["activity_calories"] is activity_calories
    assert result["weekly_budget"]["consumed_calories"] == consumed


# --- Weekly budget (real WeeklyBudgetService math) -------------------------


class _MovementEntries:
    def __init__(self, entries):
        self.entries = entries
        self.calls = 0

    async def fetch_included_kcal_for_range(self, user_id, start_utc, end_utc):
        self.calls += 1
        return [
            (logged_at, kcal)
            for logged_at, kcal in self.entries
            if start_utc <= logged_at < end_utc
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("activity_calories", "consumed", "adjusted"),
    [(True, 2100.0, 1983.3), (False, 2300.0, 1950.0)],
)
async def test_weekly_budget_counts_movement_only_while_activity_calories_on(
    activity_calories, consumed, adjusted
):
    """Monday: 2300 kcal of food and a 200 kcal workout; Tuesday's budget."""
    week_start = date(2026, 3, 9)
    weekly_budget = WeeklyMacroBudget(
        weekly_budget_id="budget-1",
        user_id="u1",
        week_start_date=week_start,
        target_calories=14000.0,
        target_protein=700.0,
        target_carbs=1750.0,
        target_fat=466.6667,
        target_revision=1,
    )
    uow = _async_uow()
    uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)
    uow.users.get_activity_calories = AsyncMock(return_value=activity_calories)
    uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=weekly_budget)
    uow.weekly_budgets.update = AsyncMock()
    uow.cheat_days.find_by_user_and_date_range = AsyncMock(return_value=[])
    uow.meals.get_daily_meal_counts = AsyncMock(return_value={week_start: 1})
    uow.meals.find_by_date_range = AsyncMock(
        return_value=[_meal(datetime(2026, 3, 9, 12, tzinfo=UTC))]
    )
    uow.hydration_entries.find_by_date_range = AsyncMock(return_value=[])
    movement = _MovementEntries([(datetime(2026, 3, 9, 18, tzinfo=UTC), _BURN_KCAL)])
    uow.movement_entries = movement
    handler = GetWeeklyBudgetQueryHandler()

    with (
        patch(f"{_WEEKLY}.AsyncUnitOfWork", return_value=uow),
        patch(
            f"{_WEEKLY}.resolve_user_timezone_async",
            AsyncMock(return_value="UTC"),
        ),
        patch(f"{_WEEKLY}.get_zone_info", return_value=ZoneInfo("UTC")),
        patch(f"{_WEEKLY}.get_user_monday", return_value=week_start),
        patch.object(
            handler,
            "_sync_targets_if_stale",
            AsyncMock(return_value=(weekly_budget, 1600.0)),
        ),
        patch.object(
            handler,
            "_resolve_tdee",
            AsyncMock(
                return_value={
                    "macro_preset": "standard",
                    "is_custom": False,
                    "profile_target_revision": 1,
                    "bmr": 1600.0,
                    "macros": {"protein": 100.0, "carbs": 250.0, "fat": 66.6667},
                }
            ),
        ),
    ):
        result = await handler.handle(
            GetWeeklyBudgetQuery(
                user_id="u1",
                target_date=date(2026, 3, 10),
                header_timezone="UTC",
            )
        )

    assert result["consumed_calories"] == consumed
    assert result["remaining_calories"] == 14000.0 - consumed
    assert result["adjusted_daily_calories"] == pytest.approx(adjusted, abs=1.0)
    assert result["activity_calories"] is activity_calories
    # Off skips the movement query altogether.
    assert movement.calls == (1 if activity_calories else 0)
