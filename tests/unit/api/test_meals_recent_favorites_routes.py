"""Unit tests for recent and favorite meal API routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from src.api.routes.v1.meals_recent_favorites import (
    favorite_meal,
    list_favorite_meals,
    list_recent_meals,
    repeat_meal,
    unfavorite_meal,
)
from src.api.schemas.request.meal_requests import RepeatMealRequest
from src.app.commands.meal import (
    FavoriteMealCommand,
    RepeatMealCommand,
    UnfavoriteMealCommand,
)
from src.app.queries.meal import (
    GetFavoriteMealsQuery,
    GetRecentMealsQuery,
)
from src.app.queries.user import GetUserTimezoneQuery
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition


def _make_meal(user_id: str) -> Meal:
    now = datetime.now(UTC)
    return Meal(
        meal_id=str(uuid4()),
        user_id=user_id,
        status=MealStatus.READY,
        dish_name="Pho Bo",
        created_at=now,
        ready_at=now,
        image=None,
        nutrition=Nutrition(
            macros=Macros(protein=25, carbs=40, fat=8),
            food_items=[
                FoodItem(
                    id=str(uuid4()),
                    name="Beef Noodles",
                    quantity=300,
                    unit="g",
                    macros=Macros(protein=25, carbs=40, fat=8),
                )
            ],
        ),
        meal_type="lunch",
    )


@pytest.mark.unit
class TestMealsRecentFavoritesRoutes:
    @pytest.mark.asyncio
    async def test_list_recent_meals_route(self):
        user_id = str(uuid4())
        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"X-Timezone": "Asia/Ho_Chi_Minh"}

        event_bus = MagicMock()

        async def _mock_send(event):
            if isinstance(event, GetUserTimezoneQuery):
                return "Asia/Ho_Chi_Minh"
            if isinstance(event, GetRecentMealsQuery):
                assert event.user_id == user_id
                assert event.user_timezone == "Asia/Ho_Chi_Minh"
                assert event.limit == 10
                return {"items": [], "total": 0}
            raise AssertionError(f"Unexpected query: {event}")

        event_bus.send = AsyncMock(side_effect=_mock_send)

        response = await list_recent_meals(
            request=mock_request,
            limit=10,
            user_id=user_id,
            language="vi",
            event_bus=event_bus,
        )

        assert response.items == []
        assert response.total == 0

    @pytest.mark.asyncio
    async def test_list_favorite_meals_route(self):
        user_id = str(uuid4())
        event_bus = MagicMock()

        async def _mock_send(event):
            if isinstance(event, GetFavoriteMealsQuery):
                assert event.user_id == user_id
                assert event.limit == 20
                return {"items": [], "total": 0}
            raise AssertionError(f"Unexpected query: {event}")

        event_bus.send = AsyncMock(side_effect=_mock_send)

        response = await list_favorite_meals(
            limit=20,
            user_id=user_id,
            language="en",
            event_bus=event_bus,
        )

        assert response.items == []
        assert response.total == 0

    @pytest.mark.asyncio
    async def test_favorite_meal_route(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())
        now = datetime.now(UTC)

        event_bus = MagicMock()

        async def _mock_send(command):
            assert isinstance(command, FavoriteMealCommand)
            assert command.user_id == user_id
            assert command.meal_id == meal_id
            return {
                "meal_id": meal_id,
                "is_favorite": True,
                "favorited_at": now,
            }

        event_bus.send = AsyncMock(side_effect=_mock_send)

        response = await favorite_meal(
            meal_id=meal_id,
            user_id=user_id,
            event_bus=event_bus,
        )

        assert response.meal_id == meal_id
        assert response.is_favorite is True
        assert response.favorited_at == now

    @pytest.mark.asyncio
    async def test_unfavorite_meal_route(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())

        event_bus = MagicMock()

        async def _mock_send(command):
            assert isinstance(command, UnfavoriteMealCommand)
            assert command.user_id == user_id
            assert command.meal_id == meal_id
            return {
                "meal_id": meal_id,
                "is_favorite": False,
                "favorited_at": None,
            }

        event_bus.send = AsyncMock(side_effect=_mock_send)

        response = await unfavorite_meal(
            meal_id=meal_id,
            user_id=user_id,
            event_bus=event_bus,
        )

        assert response.meal_id == meal_id
        assert response.is_favorite is False
        assert response.favorited_at is None

    @pytest.mark.asyncio
    async def test_repeat_meal_route(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _make_meal(user_id)

        event_bus = MagicMock()

        async def _mock_send(command):
            assert isinstance(command, RepeatMealCommand)
            assert command.user_id == user_id
            assert command.meal_id == meal_id
            assert command.idempotency_key == "idempotent-key-1"
            assert command.meal_type == "dinner"
            return meal

        event_bus.send = AsyncMock(side_effect=_mock_send)

        response = await repeat_meal(
            meal_id=meal_id,
            payload=RepeatMealRequest(meal_type="dinner"),
            idempotency_key="idempotent-key-1",
            user_id=user_id,
            language="vi",
            event_bus=event_bus,
        )

        assert response.meal_id == meal.meal_id
        assert response.dish_name == "Pho Bo"
