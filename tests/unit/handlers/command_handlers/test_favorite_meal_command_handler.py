"""Unit tests for FavoriteMeal and UnfavoriteMeal command handlers."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.exceptions import (
    AuthorizationException,
    ConflictException,
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
from src.domain.model.nutrition import FoodItem, Macros, Nutrition
from src.domain.ports.favorite_meal_repository_port import MAX_FAVORITE_MEALS


def _create_meal(
    user_id: str,
    meal_id: str,
    status: MealStatus = MealStatus.READY,
    meal_type: str = "lunch",
    item_name: str = "Test Item",
    quantity: float = 100.0,
) -> Meal:
    now = datetime.now(UTC)
    nutrition = (
        Nutrition(
            macros=Macros(protein=10, carbs=10, fat=10),
            food_items=[
                FoodItem(
                    id=str(uuid4()),
                    name=item_name,
                    quantity=quantity,
                    unit="g",
                    macros=Macros(protein=10, carbs=10, fat=10),
                )
            ],
        )
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


def _make_uow(meal=None, favorites=None):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.meals.find_by_id = AsyncMock(return_value=meal)
    uow.favorite_meals.list_favorite_meals = AsyncMock(return_value=favorites or [])
    uow.favorite_meals.favorite = AsyncMock(return_value=True)
    uow.favorite_meals.unfavorite = AsyncMock(return_value=True)
    return uow


@pytest.mark.unit
class TestFavoriteMealCommandHandler:
    @pytest.mark.asyncio
    async def test_favorite_meal_success(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _create_meal(user_id, meal_id)

        uow = _make_uow(meal=meal)
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
    async def test_favorite_same_meal_twice_is_idempotent(self):
        """NM-438: starring an already-favorited meal is a no-op success."""
        user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _create_meal(user_id, meal_id)
        fav_at = datetime.now(UTC)

        uow = _make_uow(meal=meal, favorites=[(meal, fav_at)])
        cache_service = MagicMock()
        cache_service.increment_revision = AsyncMock(return_value=1)

        handler = FavoriteMealCommandHandler(uow=uow, cache_service=cache_service)
        result = await handler.handle(
            FavoriteMealCommand(user_id=user_id, meal_id=meal_id)
        )

        assert result["is_favorite"] is True
        assert result["favorited_at"] == fav_at
        uow.favorite_meals.favorite.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_favorite_meal_with_same_identity_is_idempotent(self):
        """NM-438: one favorite per meal identity (same items and grams)."""
        user_id = str(uuid4())
        favorited = _create_meal(user_id, str(uuid4()), item_name="Beef", quantity=200)
        clone = _create_meal(user_id, str(uuid4()), item_name="Beef", quantity=200)
        fav_at = datetime.now(UTC)

        uow = _make_uow(meal=clone, favorites=[(favorited, fav_at)])

        handler = FavoriteMealCommandHandler(uow=uow)
        result = await handler.handle(
            FavoriteMealCommand(user_id=user_id, meal_id=clone.meal_id)
        )

        assert result["is_favorite"] is True
        assert result["favorited_at"] == fav_at
        uow.favorite_meals.favorite.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_favorite_twenty_first_meal_is_rejected_without_eviction(self):
        """NM-438: the 21st favorite is rejected; nothing is evicted."""
        user_id = str(uuid4())
        favorites = [
            (
                _create_meal(
                    user_id, str(uuid4()), item_name=f"Item {i}", quantity=100 + i
                ),
                datetime.now(UTC),
            )
            for i in range(MAX_FAVORITE_MEALS)
        ]
        new_meal = _create_meal(
            user_id, str(uuid4()), item_name="Brand New", quantity=42
        )

        uow = _make_uow(meal=new_meal, favorites=favorites)

        handler = FavoriteMealCommandHandler(uow=uow)
        with pytest.raises(ConflictException) as exc_info:
            await handler.handle(
                FavoriteMealCommand(user_id=user_id, meal_id=new_meal.meal_id)
            )

        assert exc_info.value.error_code == "FAVORITES_LIMIT_REACHED"
        uow.favorite_meals.favorite.assert_not_awaited()
        uow.favorite_meals.unfavorite.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_favorite_at_cap_still_idempotent_for_existing_favorite(self):
        """Re-starring an existing favorite succeeds even at the cap."""
        user_id = str(uuid4())
        favorites = [
            (
                _create_meal(
                    user_id, str(uuid4()), item_name=f"Item {i}", quantity=100 + i
                ),
                datetime.now(UTC),
            )
            for i in range(MAX_FAVORITE_MEALS)
        ]
        existing_meal = favorites[5][0]

        uow = _make_uow(meal=existing_meal, favorites=favorites)

        handler = FavoriteMealCommandHandler(uow=uow)
        result = await handler.handle(
            FavoriteMealCommand(user_id=user_id, meal_id=existing_meal.meal_id)
        )

        assert result["is_favorite"] is True
        uow.favorite_meals.favorite.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_favorite_nonexistent_meal_raises_not_found(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())

        uow = _make_uow(meal=None)

        handler = FavoriteMealCommandHandler(uow=uow)
        with pytest.raises(ResourceNotFoundException):
            await handler.handle(FavoriteMealCommand(user_id=user_id, meal_id=meal_id))

    @pytest.mark.asyncio
    async def test_favorite_other_user_meal_raises_authorization(self):
        owner_id = str(uuid4())
        other_user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _create_meal(owner_id, meal_id)

        uow = _make_uow(meal=meal)

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

        uow = _make_uow(meal=meal)

        handler = FavoriteMealCommandHandler(uow=uow)
        with pytest.raises(ValidationException):
            await handler.handle(FavoriteMealCommand(user_id=user_id, meal_id=meal_id))

    @pytest.mark.asyncio
    async def test_favorite_inactive_meal_raises_validation(self):
        user_id = str(uuid4())
        meal_id = str(uuid4())
        meal = _create_meal(user_id, meal_id, status=MealStatus.INACTIVE)

        uow = _make_uow(meal=meal)

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

    @pytest.mark.asyncio
    async def test_unfavorite_falls_back_to_identity_match(self):
        """Unstarring a clone removes the favorite recorded on the original."""
        user_id = str(uuid4())
        favorited = _create_meal(user_id, str(uuid4()), item_name="Pho", quantity=350)
        clone = _create_meal(user_id, str(uuid4()), item_name="Pho", quantity=350)

        uow = MagicMock()
        uow.__aenter__ = AsyncMock(return_value=uow)
        uow.__aexit__ = AsyncMock(return_value=False)
        uow.meals.find_by_id = AsyncMock(return_value=clone)
        uow.favorite_meals.unfavorite = AsyncMock(side_effect=[False, True])
        uow.favorite_meals.list_favorite_meals = AsyncMock(
            return_value=[(favorited, datetime.now(UTC))]
        )

        handler = UnfavoriteMealCommandHandler(uow=uow)
        result = await handler.handle(
            UnfavoriteMealCommand(user_id=user_id, meal_id=clone.meal_id)
        )

        assert result["is_favorite"] is False
        assert uow.favorite_meals.unfavorite.await_count == 2
        second_call_kwargs = uow.favorite_meals.unfavorite.await_args_list[1].kwargs
        assert second_call_kwargs["meal_id"] == favorited.meal_id
