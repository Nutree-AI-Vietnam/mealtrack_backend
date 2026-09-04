"""Tests that weekly budget resolves TDEE before opening its UoW (no nested checkout)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.app.handlers.query_handlers.get_weekly_budget_query_handler import (
    GetWeeklyBudgetQueryHandler,
)
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.domain.model.weekly import WeeklyMacroBudget
from src.domain.services.weekly_budget_service import (
    AdjustedDailyTargets,
    EffectiveAdjustedResult,
)


def _tdee_result(**overrides):
    base = {
        "target_calories": 2000.0,
        "macros": {"protein": 100.0, "carbs": 200.0, "fat": 66.7, "calories": 2000.0},
        "bmr": 1600.0,
        "profile_target_revision": 1,
        "macro_preset": "standard",
        "is_custom": False,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_tdee_resolved_before_weekly_budget_uow():
    """GetUserTdee must finish before weekly-budget AsyncUnitOfWork enters."""
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
            "calories": 0.0,
            "protein": 0.0,
            "carbs": 0.0,
            "fat": 0.0,
        },
        logged_past_days=0,
        skipped_days=0,
        show_logging_prompt=False,
    )

    call_order: list[str] = []
    uow_entered = False

    mock_uow = AsyncMock()

    async def _enter(_self=None):
        nonlocal uow_entered
        uow_entered = True
        call_order.append("uow_enter")
        return mock_uow

    mock_uow.__aenter__ = _enter
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)
    mock_uow.weekly_budgets.find_by_user_and_week = AsyncMock(
        return_value=weekly_budget
    )
    mock_uow.weekly_budgets.update = AsyncMock()
    mock_uow.cheat_days.find_by_user_and_date_range = AsyncMock(return_value=[])

    async def _tdee_handle(_query):
        assert not uow_entered, "TDEE must not run while weekly-budget UoW is open"
        call_order.append("tdee")
        return _tdee_result()

    handler = GetWeeklyBudgetQueryHandler()
    query = GetWeeklyBudgetQuery(
        user_id="u1",
        target_date=week_start,
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
        patch(
            "src.app.handlers.query_handlers.get_user_tdee_query_handler."
            "GetUserTdeeQueryHandler.handle",
            new_callable=AsyncMock,
            side_effect=_tdee_handle,
        ),
        patch(
            "src.app.handlers.query_handlers.get_weekly_budget_query_handler."
            "WeeklyBudgetService.get_effective_adjusted_daily_async",
            AsyncMock(return_value=effective),
        ),
    ):
        await handler.handle(query)

    assert call_order == ["tdee", "uow_enter"]
    assert call_order.count("tdee") == 1


@pytest.mark.asyncio
async def test_create_and_sync_never_construct_tdee_handler():
    """Helpers consume pre-resolved TDEE; they must not open nested TDEE UoWs."""
    handler = GetWeeklyBudgetQueryHandler()
    uow = MagicMock()
    uow.weekly_budgets.create = AsyncMock()
    uow.weekly_budgets.update = AsyncMock()
    tdee = _tdee_result(profile_target_revision=2)

    with patch(
        "src.app.handlers.query_handlers.get_user_tdee_query_handler."
        "GetUserTdeeQueryHandler"
    ) as mock_tdee_cls:
        await handler._create_weekly_budget(
            uow,
            "u1",
            date(2026, 3, 9),
            date(2026, 3, 9),
            tdee_result=tdee,
        )
        stale = WeeklyMacroBudget(
            weekly_budget_id="stale",
            user_id="u1",
            week_start_date=date(2026, 3, 9),
            target_calories=1000.0,
            target_protein=100.0,
            target_carbs=100.0,
            target_fat=100.0,
            target_revision=1,
        )
        await handler._sync_targets_if_stale(uow, stale, "u1", tdee_result=tdee)

    mock_tdee_cls.assert_not_called()
