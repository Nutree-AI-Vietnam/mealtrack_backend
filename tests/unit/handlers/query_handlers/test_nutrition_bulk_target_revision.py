from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.handlers.query_handlers.get_nutrition_bulk_query_handler import (
    GetNutritionBulkQueryHandler,
)
from src.app.queries.nutrition import GetNutritionBulkQuery
from src.domain.model.user import MacroPreset
from src.domain.services.hydration_intake import intake_watermark
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
    handler._logs_watermark = AsyncMock(return_value="wm")
    handler._compute = AsyncMock(return_value=fresh)

    assert await handler.handle(query) == fresh
    handler._compute.assert_awaited_once()
    cache.set_json.assert_awaited_once()


def _cached_bulk_handler(cached):
    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=cached)
    cache.set_json = AsyncMock()
    handler = GetNutritionBulkQueryHandler(cache_service=cache)
    handler._get_user_targets = AsyncMock(
        return_value=(2000, {}, 1700, 2, MagicMock(), False)
    )
    return handler, cache


@pytest.mark.asyncio
async def test_bulk_cache_from_before_an_edit_is_recomputed():
    """A portion edit the Queue purge has not reached must not serve old kcal."""
    query = GetNutritionBulkQuery(
        user_id="u1", start_date=date(2026, 10, 19), end_date=date(2026, 10, 25)
    )
    before_edit = {"target_revision": 2, "intake_watermark": "08:20", "dates": {}}
    handler, cache = _cached_bulk_handler(before_edit)
    handler._logs_watermark = AsyncMock(return_value="08:22")
    fresh = {"target_revision": 2, "dates": {"2026-10-25": {"food_calories": 66}}}
    handler._compute = AsyncMock(return_value=fresh)

    assert await handler.handle(query) == fresh
    handler._compute.assert_awaited_once()
    cache.set_json.assert_awaited_once()
    assert cache.set_json.await_args.args[1]["intake_watermark"] == "08:22"


@pytest.mark.asyncio
async def test_bulk_cache_with_current_log_fingerprint_is_served():
    query = GetNutritionBulkQuery(
        user_id="u1", start_date=date(2026, 10, 19), end_date=date(2026, 10, 25)
    )
    cached = {"target_revision": 2, "intake_watermark": "08:22", "dates": {}}
    handler, cache = _cached_bulk_handler(cached)
    handler._logs_watermark = AsyncMock(return_value="08:22")
    handler._compute = AsyncMock()

    assert await handler.handle(query) == cached
    handler._compute.assert_not_awaited()
    cache.set_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_without_log_fingerprint_neither_trusts_nor_writes_cache():
    query = GetNutritionBulkQuery(
        user_id="u1", start_date=date(2026, 10, 19), end_date=date(2026, 10, 25)
    )
    cached = {"target_revision": 2, "dates": {}}
    handler, cache = _cached_bulk_handler(cached)
    handler._logs_watermark = AsyncMock(return_value=None)
    fresh = {"target_revision": 2, "dates": {}}
    handler._compute = AsyncMock(return_value=fresh)

    assert await handler.handle(query) == fresh
    handler._compute.assert_awaited_once()
    cache.set_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_log_fingerprint_reads_the_whole_local_range():
    query = GetNutritionBulkQuery(
        user_id="u1",
        start_date=date(2026, 10, 19),
        end_date=date(2026, 10, 25),
        header_timezone="Asia/Ho_Chi_Minh",
    )
    meal_at = datetime(2026, 10, 25, 1, 22, tzinfo=UTC)
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.meals.max_updated_at_for_local_range = AsyncMock(return_value=meal_at)
    uow.hydration_entries.max_updated_at_for_local_range = AsyncMock(
        return_value=None
    )
    uow.movement_entries.max_updated_at_for_local_range = AsyncMock(
        return_value=None
    )
    handler = GetNutritionBulkQueryHandler()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler.AsyncUnitOfWork",
            lambda: uow,
        )
        monkeypatch.setattr(
            "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler.resolve_user_timezone_async",
            AsyncMock(return_value="Asia/Ho_Chi_Minh"),
        )
        watermark = await handler._logs_watermark(query)

    assert watermark == intake_watermark(meal_at, None, None)
    uow.meals.max_updated_at_for_local_range.assert_awaited_once_with(
        date(2026, 10, 19), date(2026, 10, 25), "u1", "Asia/Ho_Chi_Minh"
    )


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


@pytest.mark.asyncio
async def test_bulk_repairs_stale_weekly_revision_before_adjusted_summary():
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
    uow.weekly_budgets.update = AsyncMock(return_value=budget)
    uow.users.get_weekly_auto_adjust = AsyncMock(return_value=True)
    effective = EffectiveAdjustedResult(
        adjusted=AdjustedDailyTargets(1900.0, 100.0, 100.0, 100.0, False, 7),
        consumed_before_today={},
        consumed_total={"calories": 0.0},
        logged_past_days=0,
        skipped_days=0,
        show_logging_prompt=False,
    )
    handler = GetNutritionBulkQueryHandler()
    handler._get_user_targets = AsyncMock(
        return_value=(
            2000.0,
            {"protein": 150.0, "carbs": 200.0, "fat": 65.0},
            1600.0,
            7,
            MacroPreset.STANDARD,
            False,
        )
    )

    async def _repair(_uow, weekly_budget, **kwargs):
        weekly_budget.target_revision = 7
        return weekly_budget

    with pytest.MonkeyPatch.context() as monkeypatch:
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
            "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler.sync_weekly_budget_targets_if_stale",
            _repair,
        )
        monkeypatch.setattr(
            "src.app.handlers.query_handlers.get_nutrition_bulk_query_handler.WeeklyBudgetService.get_effective_adjusted_daily_async",
            AsyncMock(return_value=effective),
        )
        result = await handler._compute(query)

    assert result["weekly_budget"]["target_revision"] == 7
    assert result["weekly_budget"]["adjusted_daily_calories"] == 1900.0
