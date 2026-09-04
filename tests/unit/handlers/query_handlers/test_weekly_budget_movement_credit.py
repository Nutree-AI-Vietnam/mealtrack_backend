from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.api.exceptions import ExternalServiceException
from src.app.handlers.query_handlers.get_weekly_budget_query_handler import (
    GetWeeklyBudgetQueryHandler,
)
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.domain.model.user import MacroPreset
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.services.weekly_budget_service import (
    AdjustedDailyTargets,
    EffectiveAdjustedResult,
)


@pytest.mark.asyncio
async def test_weekly_budget_response_uses_movement_adjusted_calories():
    week_start = date(2026, 3, 9)
    target_date = week_start
    weekly_budget = WeeklyMacroBudget(
        weekly_budget_id="budget-1",
        user_id="u1",
        week_start_date=week_start,
        target_calories=14000.0,
        target_protein=700.0,
        target_carbs=1750.0,
        target_fat=466.6667,
    )
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

    mock_uow = AsyncMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)
    mock_uow.weekly_budgets.find_by_user_and_week.return_value = weekly_budget
    mock_uow.weekly_budgets.update = AsyncMock()
    mock_uow.cheat_days.find_by_user_and_date_range.return_value = []

    handler = GetWeeklyBudgetQueryHandler()
    query = GetWeeklyBudgetQuery(
        user_id="u1",
        target_date=target_date,
        header_timezone="UTC",
    )

    with (
        patch(
            "src.app.handlers.query_handlers.get_weekly_budget_query_handler."
            "AsyncUnitOfWork",
            return_value=mock_uow,
        ),
        patch(
            "src.app.handlers.query_handlers.get_weekly_budget_query_handler."
            "resolve_user_timezone_async",
            new_callable=AsyncMock,
            return_value="UTC",
        ),
        patch(
            "src.app.handlers.query_handlers.get_weekly_budget_query_handler."
            "get_zone_info",
            return_value=ZoneInfo("UTC"),
        ),
        patch(
            "src.app.handlers.query_handlers.get_weekly_budget_query_handler."
            "get_user_monday",
            return_value=week_start,
        ),
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
                    "macros": {
                        "protein": 100.0,
                        "carbs": 250.0,
                        "fat": 66.7,
                        "calories": 2000.0,
                    },
                }
            ),
        ),
        patch(
            "src.app.handlers.query_handlers.get_weekly_budget_query_handler."
            "WeeklyBudgetService.get_effective_adjusted_daily_async",
            AsyncMock(return_value=effective),
        ),
    ):
        result = await handler.handle(query)

    assert result["consumed_calories"] == 2100.0
    assert result["remaining_calories"] == 11900.0
    assert result["preview_tomorrow_calories"] == pytest.approx(1983.3, abs=1.0)
    assert weekly_budget.consumed_calories == 2100.0
    assert weekly_budget.consumed_protein == 100.0
    assert weekly_budget.consumed_carbs == 250.0
    assert weekly_budget.consumed_fat == 100.0


@pytest.mark.asyncio
async def test_sync_targets_refreshes_macro_only_changes():
    weekly_budget = WeeklyMacroBudget(
        weekly_budget_id="budget-1",
        user_id="u1",
        week_start_date=date(2026, 3, 9),
        target_calories=14000.0,
        target_protein=1008.0,
        target_carbs=1750.0,
        target_fat=466.7,
    )
    mock_uow = MagicMock()
    mock_uow.weekly_budgets.update = AsyncMock()
    handler = GetWeeklyBudgetQueryHandler()
    tdee_result = {
        "target_calories": 2000.0,
        "macros": {
            "protein": 160.0,
            "carbs": 230.0,
            "fat": 71.1,
        },
        "bmr": 1600.0,
        "profile_target_revision": 1,
        "macro_preset": "standard",
        "is_custom": False,
    }

    updated_budget, bmr = await handler._sync_targets_if_stale(
        mock_uow,
        weekly_budget,
        "u1",
        tdee_result=tdee_result,
    )

    assert updated_budget is weekly_budget
    assert bmr == 1600.0
    assert weekly_budget.target_calories == 15399.3
    assert weekly_budget.target_protein == 1120.0
    assert weekly_budget.target_carbs == 1610.0
    assert weekly_budget.target_fat == pytest.approx(497.7)
    mock_uow.weekly_budgets.update.assert_awaited_once_with(weekly_budget)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_custom", "macros"),
    [
        (False, {"protein": 95.0, "carbs": 23.8, "fat": 158.3}),
        (True, {"protein": 100.0, "carbs": 123.5, "fat": 50.0}),
    ],
    ids=["keto", "custom"],
)
async def test_weekly_create_and_stale_sync_derive_calories_from_macros(
    is_custom, macros
):
    handler = GetWeeklyBudgetQueryHandler()
    uow = MagicMock()
    uow.weekly_budgets.create = AsyncMock()
    uow.weekly_budgets.update = AsyncMock()
    daily_calories = round(
        macros["protein"] * 4 + macros["carbs"] * 4 + macros["fat"] * 9,
        1,
    )
    tdee_result = {
        "target_calories": 1900.0,
        "macros": {**macros, "calories": daily_calories},
        "bmr": 1600.0,
        "profile_target_revision": 2,
        "macro_preset": "standard" if is_custom else "keto",
        "is_custom": is_custom,
    }

    created, _ = await handler._create_weekly_budget(
        uow,
        "u1",
        date(2026, 3, 9),
        date(2026, 3, 9),
        tdee_result=tdee_result,
    )
    stale = WeeklyMacroBudget(
        weekly_budget_id="stale",
        user_id="u1",
        week_start_date=date(2026, 3, 9),
        target_calories=13300.0,
        target_protein=created.target_protein,
        target_carbs=created.target_carbs,
        target_fat=created.target_fat,
        target_revision=1,
    )
    synced, _ = await handler._sync_targets_if_stale(
        uow, stale, "u1", tdee_result=tdee_result
    )

    expected_weekly_calories = round(
        created.target_protein * 4 + created.target_carbs * 4 + created.target_fat * 9,
        1,
    )
    assert created.target_calories == expected_weekly_calories
    assert synced.target_calories == expected_weekly_calories
    assert synced.target_calories == round(daily_calories * 7, 1)


@pytest.mark.asyncio
async def test_read_only_budget_projection_does_not_create_or_update_budget():
    handler = GetWeeklyBudgetQueryHandler()
    uow = MagicMock()
    uow.weekly_budgets.create = AsyncMock()
    uow.weekly_budgets.update = AsyncMock()
    tdee_result = {
        "macros": {"protein": 100.0, "carbs": 200.0, "fat": 66.7},
        "bmr": 1600.0,
        "profile_target_revision": 2,
        "macro_preset": "standard",
        "is_custom": False,
    }

    created, _ = await handler._create_weekly_budget(
        uow,
        "u1",
        date(2026, 3, 9),
        date(2026, 3, 9),
        tdee_result=tdee_result,
        persist=False,
    )
    stale = WeeklyMacroBudget(
        weekly_budget_id="stale",
        user_id="u1",
        week_start_date=date(2026, 3, 9),
        target_calories=13300.0,
        target_protein=created.target_protein,
        target_carbs=created.target_carbs,
        target_fat=created.target_fat,
        target_revision=1,
    )
    await handler._sync_targets_if_stale(
        uow, stale, "u1", tdee_result=tdee_result, persist=False
    )

    uow.weekly_budgets.create.assert_not_awaited()
    uow.weekly_budgets.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_authoritative_target_writes_no_weekly_budget():
    handler = GetWeeklyBudgetQueryHandler()
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.weekly_budgets.create = AsyncMock()

    with (
        patch(
            "src.app.handlers.query_handlers.get_user_tdee_query_handler."
            "GetUserTdeeQueryHandler.handle",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unavailable"),
        ),
        patch(
            "src.app.handlers.query_handlers.get_weekly_budget_query_handler."
            "AsyncUnitOfWork",
            return_value=uow,
        ),
    ):
        with pytest.raises(ExternalServiceException) as error:
            await handler.handle(
                GetWeeklyBudgetQuery(
                    user_id="u1",
                    target_date=date(2026, 3, 9),
                    header_timezone="UTC",
                )
            )

    assert error.value.error_code == "target_unavailable"
    uow.weekly_budgets.create.assert_not_awaited()
    uow.__aenter__.assert_not_awaited()


def test_next_day_keto_cap_preserves_redistributed_macros():
    capped = AdjustedDailyTargets(
        calories=1900.0,
        carbs=10.0,
        fat=200.0,
        protein=150.0,
        bmr_floor_active=False,
        remaining_days=2,
    )

    result = GetWeeklyBudgetQueryHandler._apply_target_policy(
        capped, (MacroPreset.KETO, False)
    )

    assert (result.protein, result.carbs, result.fat) == (150.0, 10.0, 200.0)
    assert result.calories == 1900.0
