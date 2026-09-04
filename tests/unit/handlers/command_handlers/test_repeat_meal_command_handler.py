"""Unit tests for RepeatMealCommandHandler."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.exceptions import (
    AuthorizationException,
    ValidationException,
)
from src.app.commands.meal import RepeatMealCommand
from src.app.handlers.command_handlers.repeat_meal_command_handler import (
    RepeatMealCommandHandler,
)
from src.domain.model.meal import Meal, MealImage, MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition


def _create_full_meal(
    user_id: str,
    meal_id: str,
    status: MealStatus = MealStatus.READY,
    meal_type: str = "lunch",
) -> Meal:
    now = datetime.now(UTC)
    food_items = [
        FoodItem(
            id=str(uuid4()),
            name="Grilled Chicken",
            quantity=150.0,
            unit="g",
            macros=Macros(protein=45.0, carbs=0.0, fat=5.0),
        )
    ]
    nutrition = Nutrition(
        macros=Macros(protein=45.0, carbs=0.0, fat=5.0),
        food_items=food_items,
    )
    image = MealImage(
        image_id=str(uuid4()),
        format="jpeg",
        size_bytes=1024,
        width=800,
        height=600,
        url="https://images.example.com/meal.jpg",
    )
    return Meal(
        meal_id=meal_id,
        user_id=user_id,
        status=status,
        dish_name="Chicken Dish",
        created_at=now,
        ready_at=now if status == MealStatus.READY else None,
        image=image,
        nutrition=nutrition,
        meal_type=meal_type,
    )


@pytest.mark.unit
class TestRepeatMealCommandHandler:
    @pytest.mark.asyncio
    async def test_repeat_meal_clones_all_components_and_saves(self):
        user_id = str(uuid4())
        source_meal_id = str(uuid4())
        source_meal = _create_full_meal(user_id, source_meal_id)

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=source_meal)
        uow.favorite_meals.is_favorite = AsyncMock(return_value=False)

        reservation = SimpleNamespace(state="active", target_meal_id=None)
        uow.meal_write_operations.reserve = AsyncMock(return_value=reservation)
        uow.meal_write_operations.complete = AsyncMock()

        async def _mock_insert(meal: Meal):
            return meal

        uow.meals.insert = AsyncMock(side_effect=_mock_insert)

        event_publisher = MagicMock()
        event_publisher.publish = AsyncMock()
        cache_service = MagicMock()
        cache_service.increment_revision = AsyncMock(return_value=1)

        handler = RepeatMealCommandHandler(
            uow=uow,
            event_publisher=event_publisher,
            cache_service=cache_service,
            environment="test",
        )

        command = RepeatMealCommand(
            user_id=user_id,
            meal_id=source_meal_id,
            idempotency_key="key-123",
            meal_type="dinner",
        )

        result = await handler.handle(command)

        assert result.meal_id != source_meal_id
        assert result.user_id == user_id
        assert result.status == MealStatus.READY
        assert result.meal_type == "dinner"
        assert result.dish_name == "Chicken Dish"
        assert result.source == "repeat"
        assert result.image is not None
        assert result.image.url == "https://images.example.com/meal.jpg"
        assert result.image.image_id != source_meal.image.image_id
        assert result.nutrition is not None
        assert len(result.nutrition.food_items) == 1
        assert (
            result.nutrition.food_items[0].id != source_meal.nutrition.food_items[0].id
        )

        uow.meal_write_operations.complete.assert_awaited_once()
        event_publisher.publish.assert_awaited_once()
        cache_service.increment_revision.assert_awaited_once_with(user_id)

    @pytest.mark.asyncio
    async def test_repeat_inactive_favorited_meal_allowed(self):
        user_id = str(uuid4())
        source_meal_id = str(uuid4())
        source_meal = _create_full_meal(
            user_id, source_meal_id, status=MealStatus.INACTIVE
        )

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=source_meal)
        uow.favorite_meals.is_favorite = AsyncMock(return_value=True)

        reservation = SimpleNamespace(state="active", target_meal_id=None)
        uow.meal_write_operations.reserve = AsyncMock(return_value=reservation)
        uow.meal_write_operations.complete = AsyncMock()
        uow.meals.insert = AsyncMock(side_effect=lambda m: m)

        handler = RepeatMealCommandHandler(uow=uow)
        command = RepeatMealCommand(
            user_id=user_id,
            meal_id=source_meal_id,
            idempotency_key="key-456",
        )

        result = await handler.handle(command)
        assert result.status == MealStatus.READY
        assert result.meal_id != source_meal_id

    @pytest.mark.asyncio
    async def test_repeat_inactive_not_favorited_raises_validation(self):
        user_id = str(uuid4())
        source_meal_id = str(uuid4())
        source_meal = _create_full_meal(
            user_id, source_meal_id, status=MealStatus.INACTIVE
        )

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=source_meal)
        uow.favorite_meals.is_favorite = AsyncMock(return_value=False)

        handler = RepeatMealCommandHandler(uow=uow)
        command = RepeatMealCommand(
            user_id=user_id,
            meal_id=source_meal_id,
            idempotency_key="key-789",
        )

        with pytest.raises(ValidationException):
            await handler.handle(command)

    @pytest.mark.asyncio
    async def test_repeat_other_user_meal_raises_authorization(self):
        owner_id = str(uuid4())
        other_user_id = str(uuid4())
        source_meal_id = str(uuid4())
        source_meal = _create_full_meal(owner_id, source_meal_id)

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=source_meal)

        handler = RepeatMealCommandHandler(uow=uow)
        command = RepeatMealCommand(
            user_id=other_user_id,
            meal_id=source_meal_id,
            idempotency_key="key-auth",
        )

        with pytest.raises(AuthorizationException):
            await handler.handle(command)

    @pytest.mark.asyncio
    async def test_repeat_idempotency_replay(self):
        user_id = str(uuid4())
        source_meal_id = str(uuid4())
        replayed_meal_id = str(uuid4())
        source_meal = _create_full_meal(user_id, source_meal_id)
        replayed_meal = _create_full_meal(user_id, replayed_meal_id)

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)

        async def _find_by_id(mid, **kwargs):
            if mid == source_meal_id:
                return source_meal
            if mid == replayed_meal_id:
                return replayed_meal
            return None

        uow.meals.find_by_id = AsyncMock(side_effect=_find_by_id)
        uow.favorite_meals.is_favorite = AsyncMock(return_value=False)

        reservation = SimpleNamespace(state="replay", target_meal_id=replayed_meal_id)
        uow.meal_write_operations.reserve = AsyncMock(return_value=reservation)

        handler = RepeatMealCommandHandler(uow=uow)
        command = RepeatMealCommand(
            user_id=user_id,
            meal_id=source_meal_id,
            idempotency_key="key-replay",
        )

        result = await handler.handle(command)
        assert result.meal_id == replayed_meal_id
        uow.meals.insert.assert_not_called()
