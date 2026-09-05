from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.handlers.query_handlers.get_nutrition_bulk_query_handler import (
    GetNutritionBulkQueryHandler,
)
from src.app.queries.nutrition import GetNutritionBulkQuery
from src.domain.model.user import MacroPreset
from src.domain.services.weekly_budget_service import (
    AdjustedDailyTargets,
    EffectiveAdjustedResult,
)


@pytest.mark.asyncio
async def test_stale_bulk_target_cache_is_recomputed():
    cache = MagicMock()
    cache.get_json = AsyncMock(return_value={"target_revision": 1})
    cache.set_json = AsyncMock()
    handler = GetNutritionBulkQueryHandler(cache_service=cache)
    query = GetNutritionBulkQuery(
        user_id="u1", start_date=date(2026, 4, 1), end_date=date(2026, 4, 2)
    )
    fresh = {"target_revision": 2, "dates": {}}
    handler._get_user_targets = AsyncMock(return_value=(2000, {}, 1700, 2, MagicMock(), False))
    handler._compute = AsyncMock(return_value=fresh)

    assert await handler.handle(query) == fresh
    handler._compute.assert_awaited_once()
    cache.set_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_bulk_preserves_adjusted_macros_for_keto_users():
    query = GetNutritionBulkQuery(
        user_id="u1", start_date=date(2026, 4, 1), end_date=date(2026, 4, 1)
    )
    budget = MagicMock(target_revision=1, target_calories=14000.0)
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.meals.find_by_date_range = AsyncMock(return_value=[])
    uow.hydration_entries.find_by_date_range = AsyncMock(return_value=[])
    uow.movement_entries.fetch_included_kcal_for_range = AsyncMock(return_value=[])
    uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=budget)
    uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)
    effective = EffectiveAdjustedResult(
        adjusted=AdjustedDailyTargets(1900.0, 100.0, 100.0, 100.0, False, 7),
        consumed_before_today={}, consumed_total={"calories": 0.0},
        logged_past_days=0, skipped_days=0, show_logging_prompt=False,
    )
    handler = GetNutritionBulkQueryHandler()
    handler._get_user_targets = AsyncMock(
        return_value=(2000.0, {}, 1600.0, 1, MacroPreset.KETO, False)
    )

    with (
        pytest.MonkeyPatch.context() as monkeypatch,
    ):
        monkeypatch.setattr(
            "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler.AsyncUnitOfWork",
            lambda: uow,
        )
        monkeypatch.setattr(
            "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler.resolve_user_timezone_async",
            AsyncMock(return_value="UTC"),
        )
        monkeypatch.setattr(
            "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler.get_user_monday",
            lambda *_: date(2026, 3, 30),
        )
        monkeypatch.setattr(
            "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler.WeeklyBudgetService.get_effective_adjusted_daily_async",
            AsyncMock(return_value=effective),
        )
        result = await handler._compute(query)

    summary = result["weekly_budget"]
    assert summary["adjusted_daily_calories"] == 1900.0
    assert summary["adjusted_daily_protein"] == 100.0
    assert summary["adjusted_daily_carbs"] == 100.0
    assert summary["adjusted_daily_fat"] == 100.0
