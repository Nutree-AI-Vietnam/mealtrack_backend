"""Unit tests for GetRecentMealsQueryHandler and GetFavoriteMealsQueryHandler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

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


def _make_meal(
    user_id: str,
    dish_name: str,
    item_name: str | None = None,
    quantity: float = 100.0,
) -> Meal:
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
                    name=item_name or dish_name,
                    quantity=quantity,
                    unit="g",
                    macros=Macros(protein=20.0, carbs=30.0, fat=10.0),
                )
            ],
        ),
        meal_type="lunch",
    )


def _make_uow(raw_meals, favorites=None):
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.meals.find_recent_food_meals = AsyncMock(return_value=raw_meals)
    mock_uow.favorite_meals.list_favorite_meals = AsyncMock(
        return_value=favorites or []
    )
    return mock_uow


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
            limit=10,
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
        meal1 = _make_meal(user_id, "Salad", item_name="Lettuce")
        meal2 = _make_meal(user_id, "Steak", item_name="Beef")

        cache_service = MagicMock()
        cache_service.get_revision = AsyncMock(return_value=1)
        cache_service.get_recent_meals = AsyncMock(return_value=None)
        cache_service.set_recent_meals = AsyncMock()

        fav_dt = datetime.now(UTC)
        mock_uow = _make_uow([meal1, meal2], favorites=[(meal1, fav_dt)])

        query = GetRecentMealsQuery(
            user_id=user_id,
            user_timezone="America/New_York",
            limit=10,
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
    async def test_recent_meals_uses_seven_day_window_in_user_timezone(self):
        """NM-437: window covers today plus the previous 6 local calendar days."""
        user_id = str(uuid4())
        tz_name = "Asia/Ho_Chi_Minh"
        mock_uow = _make_uow([])

        handler = GetRecentMealsQueryHandler(uow_factory=lambda: mock_uow)
        query = GetRecentMealsQuery(user_id=user_id, user_timezone=tz_name)

        await handler.handle(query)

        call_kwargs = mock_uow.meals.find_recent_food_meals.await_args.kwargs
        tz = ZoneInfo(tz_name)
        start_local = call_kwargs["start_dt"].astimezone(tz)
        end_local = call_kwargs["end_dt"].astimezone(tz)
        today_local = datetime.now(tz).date()

        assert start_local.date() == today_local - timedelta(days=6)
        assert start_local.time() == datetime.min.time()
        assert end_local.date() == today_local + timedelta(days=1)
        assert end_local.time() == datetime.min.time()

    @pytest.mark.asyncio
    async def test_recent_meals_returns_ten_distinct_meals_by_default(self):
        """NM-437: last 10 DISTINCT meals; duplicates by identity are skipped."""
        user_id = str(uuid4())
        distinct = [
            _make_meal(user_id, f"Dish {i}", item_name=f"Item {i}") for i in range(12)
        ]
        # A duplicate of the newest meal (same item, same grams, other name)
        duplicate = _make_meal(user_id, "Renamed Dish 0", item_name="Item 0")
        raw = [distinct[0], duplicate, *distinct[1:]]

        mock_uow = _make_uow(raw)
        handler = GetRecentMealsQueryHandler(uow_factory=lambda: mock_uow)
        query = GetRecentMealsQuery(user_id=user_id, user_timezone="UTC")

        result = await handler.handle(query)

        assert result["total"] == 10
        returned_ids = [item["meal_id"] for item in result["items"]]
        assert duplicate.meal_id not in returned_ids
        assert returned_ids == [m.meal_id for m in distinct[:10]]

    @pytest.mark.asyncio
    async def test_recent_meals_flags_favorite_by_identity_not_meal_id(self):
        """A recent meal is flagged favorite when a favorite shares its identity."""
        user_id = str(uuid4())
        recent_clone = _make_meal(user_id, "Pho Bo", item_name="Beef Noodles")
        older_favorited = _make_meal(user_id, "Pho Bo", item_name="Beef Noodles")
        assert recent_clone.meal_id != older_favorited.meal_id

        mock_uow = _make_uow(
            [recent_clone],
            favorites=[(older_favorited, datetime.now(UTC))],
        )
        handler = GetRecentMealsQueryHandler(uow_factory=lambda: mock_uow)
        query = GetRecentMealsQuery(user_id=user_id, user_timezone="UTC")

        result = await handler.handle(query)

        assert result["total"] == 1
        assert result["items"][0]["meal_id"] == recent_clone.meal_id
        assert result["items"][0]["is_favorite"] is True


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
        query = GetFavoriteMealsQuery(user_id="u-456", limit=20, language="en")

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

        query = GetFavoriteMealsQuery(user_id=user_id, limit=20, language="en")

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
