"""Tests for GetDailyBreakdownQueryHandler latency-oriented behavior."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.handlers.query_handlers.get_daily_breakdown_query_handler import (
    GetDailyBreakdownQueryHandler,
)
from src.app.queries.meal.get_daily_breakdown_query import GetDailyBreakdownQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import Nutrition
from src.domain.model.nutrition.macros import Macros


def _meal(created_at: datetime, protein: float) -> Meal:
    return Meal(
        meal_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        status=MealStatus.READY,
        created_at=created_at,
        image=None,
        dish_name="Test meal",
        nutrition=Nutrition(macros=Macros(protein=protein, carbs=10.0, fat=5.0)),
        ready_at=created_at,
    )


@pytest.mark.asyncio
async def test_uses_single_range_query_for_week():
    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=None)
    cache.set_json = AsyncMock()
    handler = GetDailyBreakdownQueryHandler(cache_service=cache)
    query = GetDailyBreakdownQuery(
        user_id="22222222-2222-2222-2222-222222222222",
        week_start=date(2026, 4, 13),
        header_timezone="UTC",
    )

    mock_uow = AsyncMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.meals.find_by_date_range = AsyncMock(
        return_value=[
            _meal(datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc), protein=20.0),
            _meal(datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc), protein=30.0),
        ]
    )
    mock_uow.meals.find_by_date = AsyncMock()

    with (
        patch(
            "src.app.handlers.query_handlers.get_daily_breakdown_query_handler.AsyncUnitOfWork",
            return_value=mock_uow,
        ),
        patch(
            "src.app.handlers.query_handlers.get_daily_breakdown_query_handler.resolve_user_timezone_async",
            new=AsyncMock(return_value="UTC"),
        ),
        patch.object(
            handler,
            "_get_base_daily_targets",
            AsyncMock(return_value=(2000.0, 100.0, 200.0, 70.0)),
        ),
    ):
        result = await handler.handle(query)

    mock_uow.meals.find_by_date_range.assert_awaited_once()
    mock_uow.meals.find_by_date.assert_not_awaited()
    assert result["week_start"] == "2026-04-13"
    assert len(result["days"]) == 7
    assert result["days"][0]["protein_consumed"] == 20.0
    assert result["days"][2]["protein_consumed"] == 30.0


@pytest.mark.asyncio
async def test_returns_cached_week_without_querying_meals():
    cached = {"week_start": "2026-04-13", "days": []}
    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=cached)
    cache.set_json = AsyncMock()
    handler = GetDailyBreakdownQueryHandler(cache_service=cache)
    query = GetDailyBreakdownQuery(
        user_id="22222222-2222-2222-2222-222222222222",
        week_start=date(2026, 4, 13),
        header_timezone="UTC",
    )

    mock_uow = AsyncMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.meals.find_by_date_range = AsyncMock()

    with (
        patch(
            "src.app.handlers.query_handlers.get_daily_breakdown_query_handler.AsyncUnitOfWork",
            return_value=mock_uow,
        ),
        patch(
            "src.app.handlers.query_handlers.get_daily_breakdown_query_handler.resolve_user_timezone_async",
            new=AsyncMock(return_value="UTC"),
        ),
    ):
        result = await handler.handle(query)

    assert result == cached
    mock_uow.meals.find_by_date_range.assert_not_awaited()


@pytest.mark.asyncio
async def test_base_daily_targets_passes_cache_service_to_tdee_handler():
    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=None)
    cache.set_json = AsyncMock()
    handler = GetDailyBreakdownQueryHandler(cache_service=cache)

    with patch(
        "src.app.handlers.query_handlers.get_user_tdee_query_handler.GetUserTdeeQueryHandler"
    ) as mock_tdee_cls:
        mock_tdee = MagicMock()
        mock_tdee.handle = AsyncMock(
            return_value={
                "target_calories": 2100.0,
                "macros": {"protein": 120.0, "carbs": 220.0, "fat": 70.0},
            }
        )
        mock_tdee_cls.return_value = mock_tdee

        result = await handler._get_base_daily_targets(
            "22222222-2222-2222-2222-222222222222"
        )

    mock_tdee_cls.assert_called_once_with(cache_service=cache)
    assert result == (2100.0, 120.0, 220.0, 70.0, None)


@pytest.mark.asyncio
async def test_cache_failures_are_non_fatal():
    cache = MagicMock()
    cache.get_json = AsyncMock(side_effect=RuntimeError("attached to a different loop"))
    cache.set_json = AsyncMock(side_effect=RuntimeError("attached to a different loop"))
    handler = GetDailyBreakdownQueryHandler(cache_service=cache)

    result = await handler._try_get_cached_result(
        "22222222-2222-2222-2222-222222222222", date(2026, 4, 13)
    )
    await handler._write_cache(
        "22222222-2222-2222-2222-222222222222",
        date(2026, 4, 13),
        {"days": []},
    )

    assert result is None
