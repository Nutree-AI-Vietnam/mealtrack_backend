"""Unit tests for FavoriteMeal and UnfavoriteMeal command handlers."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.exceptions import (
    AuthorizationException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.meal import FavoriteMealCommand, UnfavoriteMealCommand
from src.app.handlers.command_handlers.favorite_meal_command_handler import (
    FavoriteMealCommandHandler,
)
from src.app.handlers.command_handlers.unfavorite_meal_command_handler import (
    UnfavoriteMealCommandHandler,
)
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import Macros, Nutrition


def _create_meal(
    user_id: str,
    meal_id: str,
    status: MealStatus = MealStatus.READY,
    meal_type: str = "lunch",
) -> Meal:
    now = datetime.now(UTC)
    nutrition = (
        Nutrition(macros=Macros(protein=10, carbs=10, fat=10))
        if status == MealStatus.READY
        else None
    )
    return Meal(
        meal_id=meal_id,
        user_id=user_id,
        status=status,
        dish_name="Test Dish",
        created_at=now,
        ready_at=now if status == MealStatus.READY else None,
        image=None,
        nutrition=nutrition,
        meal_type=meal_type,
    )


@pytest.mark.unit
class TestFavoriteMealCommandHandler:
    @pytest.mark.asyncio
    async def test_favorite_meal_success(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _create_meal(user_id, meal_id)

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=meal)
        fav_time = datetime.now(UTC)
        uow.favorite_meals.favorite = AsyncMock(return_value=fav_time)

        cache_service = MagicMock()
        cache_service.increment_revision = AsyncMock(return_value=1)

        handler = FavoriteMealCommandHandler(uow=uow, cache_service=cache_service)
        result = await handler.handle(
            FavoriteMealCommand(user_id=user_id, meal_id=meal_id)
        )

        assert result["meal_id"] == meal_id
        assert result["is_favorite"] is True
        assert result["favorited_at"] is not None
        assert uow.favorite_meals.favorite.await_count == 1
        call_kwargs = uow.favorite_meals.favorite.await_args.kwargs
        assert call_kwargs["user_id"] == user_id
        assert call_kwargs["meal_id"] == meal_id
        assert call_kwargs["favorited_at"] == result["favorited_at"]
        cache_service.increment_revision.assert_awaited_once_with(user_id)

    @pytest.mark.asyncio
    async def test_favorite_nonexistent_meal_raises_not_found(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=None)

        handler = FavoriteMealCommandHandler(uow=uow)
        with pytest.raises(ResourceNotFoundException):
            await handler.handle(FavoriteMealCommand(user_id=user_id, meal_id=meal_id))

    @pytest.mark.asyncio
    async def test_favorite_other_user_meal_raises_authorization(self):
        owner_id = str(uuid4())
        other_user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _create_meal(owner_id, meal_id)

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=meal)

        handler = FavoriteMealCommandHandler(uow=uow)
        with pytest.raises(AuthorizationException):
            await handler.handle(
                FavoriteMealCommand(user_id=other_user_id, meal_id=meal_id)
            )

    @pytest.mark.asyncio
    async def test_favorite_hydration_raises_validation(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _create_meal(user_id, meal_id, meal_type="hydration")

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=meal)

        handler = FavoriteMealCommandHandler(uow=uow)
        with pytest.raises(ValidationException):
            await handler.handle(FavoriteMealCommand(user_id=user_id, meal_id=meal_id))

    @pytest.mark.asyncio
    async def test_favorite_inactive_meal_raises_validation(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _create_meal(user_id, meal_id, status=MealStatus.INACTIVE)

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=meal)

        handler = FavoriteMealCommandHandler(uow=uow)
        with pytest.raises(ValidationException):
            await handler.handle(FavoriteMealCommand(user_id=user_id, meal_id=meal_id))


@pytest.mark.unit
class TestUnfavoriteMealCommandHandler:
    @pytest.mark.asyncio
    async def test_unfavorite_meal_success(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.favorite_meals.unfavorite = AsyncMock(return_value=True)

        cache_service = MagicMock()
        cache_service.increment_revision = AsyncMock(return_value=2)

        handler = UnfavoriteMealCommandHandler(uow=uow, cache_service=cache_service)
        result = await handler.handle(
            UnfavoriteMealCommand(user_id=user_id, meal_id=meal_id)
        )

        assert result["meal_id"] == meal_id
        assert result["is_favorite"] is False
        assert result["favorited_at"] is None
        uow.favorite_meals.unfavorite.assert_awaited_once_with(
            user_id=user_id, meal_id=meal_id
        )
        cache_service.increment_revision.assert_awaited_once_with(user_id)
