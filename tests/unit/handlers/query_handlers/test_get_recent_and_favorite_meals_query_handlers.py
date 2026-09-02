"""Unit tests for GetRecentMealsQueryHandler and GetFavoriteMealsQueryHandler."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.app.handlers.query_handlers.get_favorite_meals_query_handler import (
    GetFavoriteMealsQueryHandler,
)
from src.app.handlers.query_handlers.get_recent_meals_query_handler import (
    GetRecentMealsQueryHandler,
)
from src.app.queries.meal import GetFavoriteMealsQuery, GetRecentMealsQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition


def _make_meal(user_id: str, dish_name: str, quantity: float = 100.0) -> Meal:
    """Build a READY meal; distinct quantity gives a distinct content fingerprint."""
    now = datetime.now(UTC)
    return Meal(
        meal_id=str(uuid4()),
        user_id=user_id,
        status=MealStatus.READY,
        dish_name=dish_name,
        created_at=now,
        ready_at=now,
        image=None,
        nutrition=Nutrition(
            macros=Macros(protein=20.0, carbs=30.0, fat=10.0),
            food_items=[
                FoodItem(
                    id=str(uuid4()),
                    name="Ingredient 1",
                    quantity=quantity,
                    unit="g",
                    macros=Macros(protein=20.0, carbs=30.0, fat=10.0),
                )
            ],
        ),
        meal_type="lunch",
    )


@pytest.mark.unit
class TestGetRecentMealsQueryHandler:
    @pytest.mark.asyncio
    async def test_recent_meals_cache_hit(self):
        cache_service = MagicMock()
        cache_service.get_revision = AsyncMock(return_value=2)
        cached_payload = {
            "items": [{"meal_id": "cached-1", "dish_name": "Cached Meal"}],
            "total": 1,
        }
        cache_service.get_recent_meals = AsyncMock(return_value=cached_payload)

        handler = GetRecentMealsQueryHandler(cache_service=cache_service)
        query = GetRecentMealsQuery(
            user_id="u-123",
            user_timezone="UTC",
            limit=20,
            language="en",
        )

        result = await handler.handle(query)
        assert result == cached_payload
        cache_service.get_recent_meals.assert_awaited_once_with(
            user_id="u-123",
            revision=2,
            timezone_str="UTC",
            language="en",
        )

    @pytest.mark.asyncio
    async def test_recent_meals_cache_miss_queries_db_and_deduplicates(self):
        user_id = str(uuid4())
        meal1 = _make_meal(user_id, "Salad", quantity=120.0)
        meal2 = _make_meal(user_id, "Steak", quantity=250.0)

        cache_service = MagicMock()
        cache_service.get_revision = AsyncMock(return_value=1)
        cache_service.get_recent_meals = AsyncMock(return_value=None)
        cache_service.set_recent_meals = AsyncMock()

        mock_uow = MagicMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)
        mock_uow.meals.find_recent_food_meals = AsyncMock(return_value=[meal1, meal2])
        mock_uow.favorite_meals.filter_favorited_meal_ids = AsyncMock(
            return_value={meal1.meal_id}
        )

        query = GetRecentMealsQuery(
            user_id=user_id,
            user_timezone="America/New_York",
            limit=20,
            language="en",
        )

        handler = GetRecentMealsQueryHandler(
            uow_factory=lambda: mock_uow,
            cache_service=cache_service,
        )
        result = await handler.handle(query)

        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert result["items"][0]["meal_id"] == meal1.meal_id
        assert result["items"][0]["is_favorite"] is True
        assert result["items"][1]["meal_id"] == meal2.meal_id
        assert result["items"][1]["is_favorite"] is False
        cache_service.set_recent_meals.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recent_meals_uses_seven_local_calendar_days(self, monkeypatch):
        """AC: recent window is the last 7 days in the user's timezone."""
        from zoneinfo import ZoneInfo

        import src.app.handlers.query_handlers.get_recent_meals_query_handler as mod

        # 2026-09-02 01:30 UTC == 2026-09-01 21:30 in America/New_York
        frozen_now = datetime(2026, 9, 2, 1, 30, tzinfo=UTC)
        monkeypatch.setattr(mod, "utc_now", lambda: frozen_now)

        mock_uow = MagicMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)
        mock_uow.meals.find_recent_food_meals = AsyncMock(return_value=[])
        mock_uow.favorite_meals.filter_favorited_meal_ids = AsyncMock(
            return_value=set()
        )

        handler = GetRecentMealsQueryHandler(uow_factory=lambda: mock_uow)
        await handler.handle(
            GetRecentMealsQuery(
                user_id="u-1", user_timezone="America/New_York", limit=10
            )
        )

        call_kwargs = mock_uow.meals.find_recent_food_meals.await_args.kwargs
        tz = ZoneInfo("America/New_York")
        # Local today is Sep 1; window covers Aug 26 00:00 through Sep 2 00:00 local
        assert call_kwargs["start_dt"] == datetime(
            2026, 8, 26, 0, 0, tzinfo=tz
        ).astimezone(UTC)
        assert call_kwargs["end_dt"] == datetime(
            2026, 9, 2, 0, 0, tzinfo=tz
        ).astimezone(UTC)

    @pytest.mark.asyncio
    async def test_recent_meals_limit_clamped_to_ten(self):
        """AC: at most 10 distinct recent meals are returned."""
        user_id = str(uuid4())
        meals = [
            _make_meal(user_id, f"Dish {i}", quantity=100.0 + i) for i in range(15)
        ]

        mock_uow = MagicMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)
        mock_uow.meals.find_recent_food_meals = AsyncMock(return_value=meals)
        mock_uow.favorite_meals.filter_favorited_meal_ids = AsyncMock(
            return_value=set()
        )

        handler = GetRecentMealsQueryHandler(uow_factory=lambda: mock_uow)
        result = await handler.handle(
            GetRecentMealsQuery(user_id=user_id, user_timezone="UTC", limit=50)
        )

        assert result["total"] == 10
        assert len(result["items"]) == 10


@pytest.mark.unit
class TestGetFavoriteMealsQueryHandler:
    @pytest.mark.asyncio
    async def test_favorite_meals_cache_hit(self):
        cache_service = MagicMock()
        cache_service.get_revision = AsyncMock(return_value=3)
        cached_payload = {
            "items": [{"meal_id": "fav-1", "is_favorite": True}],
            "total": 1,
        }
        cache_service.get_favorite_meals = AsyncMock(return_value=cached_payload)

        handler = GetFavoriteMealsQueryHandler(cache_service=cache_service)
        query = GetFavoriteMealsQuery(user_id="u-456", limit=50, language="en")

        result = await handler.handle(query)
        assert result == cached_payload

    @pytest.mark.asyncio
    async def test_favorite_meals_cache_miss_queries_db(self):
        user_id = str(uuid4())
        meal1 = _make_meal(user_id, "Favorite Pasta")
        fav_dt = datetime.now(UTC)

        cache_service = MagicMock()
        cache_service.get_revision = AsyncMock(return_value=1)
        cache_service.get_favorite_meals = AsyncMock(return_value=None)
        cache_service.set_favorite_meals = AsyncMock()

        query = GetFavoriteMealsQuery(user_id=user_id, limit=50, language="en")

        mock_uow = MagicMock()
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=False)
        mock_uow.favorite_meals.list_favorite_meals = AsyncMock(
            return_value=[(meal1, fav_dt)]
        )

        handler = GetFavoriteMealsQueryHandler(
            uow_factory=lambda: mock_uow,
            cache_service=cache_service,
        )
        result = await handler.handle(query)

        assert result["total"] == 1
        assert result["items"][0]["meal_id"] == meal1.meal_id
        assert result["items"][0]["is_favorite"] is True
        assert result["items"][0]["favorited_at"] == fav_dt.isoformat()
        cache_service.set_favorite_meals.assert_awaited_once()
