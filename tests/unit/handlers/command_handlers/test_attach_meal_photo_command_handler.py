from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.exceptions import AuthorizationException
from src.app.commands.meal import AttachMealPhotoCommand, DeleteMealPhotoCommand
from src.app.handlers.command_handlers.attach_meal_photo_command_handler import (
    AttachMealPhotoCommandHandler,
)
from src.app.handlers.command_handlers.delete_meal_photo_command_handler import (
    DeleteMealPhotoCommandHandler,
)
from src.domain.model.meal import Meal, MealImage
from src.domain.model.nutrition import Macros, Nutrition


def _ready_meal(user_id: str) -> Meal:
    meal = Meal.create_new_processing(
        user_id=user_id,
        image=MealImage(
            image_id=str(uuid4()),
            format="jpeg",
            size_bytes=1000,
            url="https://example.com/original.jpg",
        ),
    )
    return meal.mark_ready(
        nutrition=Nutrition(macros=Macros(protein=20, carbs=30, fat=10)),
        dish_name="Rice bowl",
    )


def _uow_for(meal: Meal):
    uow = MagicMock()
    uow.events = []
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(
        side_effect=lambda *args: uow.events.append("uow_exit") or False
    )
    uow.meals.find_by_id = AsyncMock(return_value=meal)
    uow.meals.save = AsyncMock(side_effect=lambda saved: saved)
    uow.commit = AsyncMock(side_effect=lambda: uow.events.append("commit"))
    uow.rollback = AsyncMock()
    return uow


@pytest.mark.asyncio
async def test_attach_meal_photo_updates_owned_meal_image():
    user_id = str(uuid4())
    meal = _ready_meal(user_id)
    uow = _uow_for(meal)
    publisher = MagicMock()
    publisher.publish = AsyncMock()
    image_id = str(uuid4())
    image_url = f"https://res.cloudinary.com/demo/image/upload/mealtrack/{image_id}.jpg"

    handler = AttachMealPhotoCommandHandler(uow=uow, event_publisher=publisher)

    result = await handler.handle(
        AttachMealPhotoCommand(
            meal_id=meal.meal_id,
            user_id=user_id,
            image_id=image_id,
            image_url=image_url,
            image_format="jpeg",
            size_bytes=2048,
        )
    )

    saved_meal = uow.meals.save.await_args.args[0]
    assert result["success"] is True
    assert result["meal_id"] == meal.meal_id
    assert result["image_url"] == image_url
    assert saved_meal.image.image_id == image_id
    assert saved_meal.image.url == image_url
    assert saved_meal.nutrition == meal.nutrition
    uow.commit.assert_awaited_once()
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_attach_meal_photo_rejects_wrong_owner():
    meal = _ready_meal(str(uuid4()))
    uow = _uow_for(meal)
    handler = AttachMealPhotoCommandHandler(uow=uow)

    with pytest.raises(AuthorizationException):
        await handler.handle(
            AttachMealPhotoCommand(
                meal_id=meal.meal_id,
                user_id=str(uuid4()),
                image_id=str(uuid4()),
                image_url="https://res.cloudinary.com/demo/image/upload/mealtrack/photo.jpg",
                image_format="jpeg",
                size_bytes=2048,
            )
        )

    uow.meals.save.assert_not_awaited()
    uow.commit.assert_not_awaited()
    uow.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_meal_photo_detaches_owned_meal_image():
    user_id = str(uuid4())
    meal = _ready_meal(user_id)
    uow = _uow_for(meal)
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = DeleteMealPhotoCommandHandler(uow=uow, event_publisher=publisher)

    result = await handler.handle(
        DeleteMealPhotoCommand(meal_id=meal.meal_id, user_id=user_id)
    )

    saved_meal = uow.meals.save.await_args.args[0]
    assert result == {
        "success": True,
        "meal_id": meal.meal_id,
        "image_url": None,
    }
    assert saved_meal.image is None
    assert saved_meal.nutrition == meal.nutrition
    uow.commit.assert_awaited_once()
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_meal_photo_rejects_wrong_owner():
    meal = _ready_meal(str(uuid4()))
    uow = _uow_for(meal)
    handler = DeleteMealPhotoCommandHandler(uow=uow)

    with pytest.raises(AuthorizationException):
        await handler.handle(
            DeleteMealPhotoCommand(meal_id=meal.meal_id, user_id=str(uuid4()))
        )

    uow.meals.save.assert_not_awaited()
    uow.commit.assert_not_awaited()
    uow.rollback.assert_awaited_once()
