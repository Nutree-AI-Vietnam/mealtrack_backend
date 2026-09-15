"""daily_macros must not discard freshly computed meal totals for a stale Redis hit."""

from datetime import UTC, date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.handlers.query_handlers.get_daily_macros_query_handler import (
    GetDailyMacrosQueryHandler,
)
from src.app.queries.meal import GetDailyMacrosQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.nutrition import Nutrition


def _meal_with_macros(
    *, meal_id: str, protein: float, carbs: float, fat: float
) -> Meal:
    from datetime import datetime

    macros = Macros(protein=protein, carbs=carbs, fat=fat, fiber=0)
    now = datetime.now(UTC)
    return Meal(
        meal_id=meal_id,
        user_id="22222222-2222-2222-2222-222222222222",
        status=MealStatus.READY,
        created_at=now,
        image=None,
        dish_name="shrimp",
        nutrition=Nutrition(macros=macros),
        meal_type="snack",
        source="manual",
        ready_at=now,
    )


@pytest.mark.asyncio
async def test_cache_hit_short_circuits_before_meal_query():
    """Valid Redis hit returns immediately and skips find_by_date."""
    cache = MagicMock()
    cached = {
        "date": "2026-08-11",
        "user_id": "u1",
        "total_calories": 0.0,
        "food_calories": 0.0,
        "total_protein": 0.0,
        "total_carbs": 0.0,
        "total_fat": 0.0,
        "meal_count": 0,
        "target_revision": 3,
        "profile_target_revision": 3,
    }
    cache.get_json = AsyncMock(return_value=cached)
    cache.set_json = AsyncMock()
    handler = GetDailyMacrosQueryHandler(cache_service=cache)
    query = GetDailyMacrosQuery(user_id="u1", target_date=date(2026, 8, 11))

    with patch(
        "src.app.handlers.query_handlers.get_daily_macros_query_handler.AsyncUnitOfWork"
    ) as mock_cls:
        mock_uow = AsyncMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)
        fake_user = MagicMock()
        fake_user.timezone = "UTC"
        mock_uow.users.find_by_id = AsyncMock(return_value=fake_user)
        mock_uow.meals.find_by_date = AsyncMock(return_value=[])
        mock_cls.return_value = mock_uow

        with patch(
            "src.app.handlers.query_handlers.get_user_tdee_query_handler.GetUserTdeeQueryHandler"
        ) as mock_tdee_cls:
            mock_tdee = MagicMock()
            mock_tdee.handle = AsyncMock(
                return_value={
                    "target_calories": 1728,
                    "macros": {"protein": 147, "carbs": 140, "fat": 64},
                    "bmr": 1400,
                    "profile_target_revision": 3,
                }
            )
            mock_tdee_cls.return_value = mock_tdee
            result = await handler.handle(query)

    assert result["total_calories"] == 0.0
    mock_uow.meals.find_by_date.assert_not_called()
    assert mock_cls.call_count == 0, "AsyncUnitOfWork should not be opened on cache hit"


@pytest.mark.asyncio
async def test_cache_miss_returns_fresh_meal_totals():
    """On cache miss, response totals come from DB meals (never discarded)."""
    cache = MagicMock()
    cache.get_json = AsyncMock(return_value=None)
    cache.set_json = AsyncMock()
    handler = GetDailyMacrosQueryHandler(cache_service=cache)
    query = GetDailyMacrosQuery(user_id="u1", target_date=date(2026, 8, 11))
    meal = _meal_with_macros(
        meal_id="11111111-1111-1111-1111-111111111111",
        protein=45,
        carbs=0,
        fat=2,
    )

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
        mock_uow.meals.find_by_date = AsyncMock(return_value=[meal])
        mock_uow.meals.sum_hydration_ml_for_date = AsyncMock(return_value=0)
        mock_uow.hydration_entries.find_by_date = AsyncMock(return_value=[])
        mock_uow.hydration_entries.sum_ml_for_date = AsyncMock(return_value=0)
        mock_uow.movement_entries.sum_included_kcal_for_range = AsyncMock(
            return_value=0.0
        )
        mock_uow.weekly_budgets.find_by_user_and_week = AsyncMock(return_value=None)
        mock_cls.return_value = mock_uow

        with patch(
            "src.app.handlers.query_handlers.get_user_tdee_query_handler.GetUserTdeeQueryHandler"
        ) as mock_tdee_cls:
            mock_tdee = MagicMock()
            mock_tdee.handle = AsyncMock(
                return_value={
                    "target_calories": 1728,
                    "macros": {"protein": 147, "carbs": 140, "fat": 64},
                    "bmr": 1400,
                    "profile_target_revision": 3,
                }
            )
            mock_tdee_cls.return_value = mock_tdee
            result = await handler.handle(query)

    assert result["total_protein"] == 45.0
    assert result["total_fat"] == 2.0
    assert result["food_calories"] == pytest.approx(45 * 4 + 2 * 9, rel=0.01)
    cache.set_json.assert_awaited()
