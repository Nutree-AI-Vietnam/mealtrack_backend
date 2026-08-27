"""Tests that upload_meal_image handler uses the injected UoW (no direct UnitOfWork() instantiation)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.handlers.command_handlers.upload_meal_image_immediately_command_handler import (
    UploadMealImageImmediatelyHandler,
)


def _make_meal_mock(meal_id=None):
    meal = MagicMock()
    meal.meal_id = meal_id or str(uuid4())
    meal.nutrition = MagicMock()
    meal.nutrition.food_items = [MagicMock()]
    meal.nutrition.calories = 400
    meal.status = MagicMock()
    return meal


_FAKE_IMAGE_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_FAKE_USER_UUID = "00000000-0000-0000-0000-000000000001"


def _make_handler(mock_uow, mock_event_bus=None):
    if mock_event_bus is None:
        mock_event_bus = MagicMock()
        mock_event_bus.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=mock_event_bus,
        event_publisher=MagicMock(publish=AsyncMock()),
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(
        return_value=f"https://res.cloudinary.com/demo/image/upload/{_FAKE_IMAGE_UUID}"
    )
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(return_value='{"dish_name": "Salad"}')
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_to_nutrition.return_value = MagicMock(
        food_items=[MagicMock()], calories=400
    )
    handler.gpt_parser.parse_dish_name.return_value = "Salad"
    handler.gpt_parser.parse_emoji.return_value = "\U0001f957"
    handler.gpt_parser.extract_raw_json.return_value = "{}"
    handler.meal_translation_service = None
    return handler


@pytest.mark.asyncio
async def test_happy_path_uses_injected_uow():
    """Happy path: handler uses self.uow context manager (no direct UnitOfWork() instantiation)."""
    mock_meal = _make_meal_mock()
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals.save = AsyncMock(return_value=mock_meal)
    mock_uow.commit = AsyncMock()

    handler = _make_handler(mock_uow)
    cmd = UploadMealImageImmediatelyCommand(
        user_id=_FAKE_USER_UUID, file_contents=b"img", content_type="image/jpeg"
    )

    await handler.handle(cmd)

    # Verify the injected UoW was used (entered as async context manager)
    mock_uow.__aenter__.assert_called()
    mock_uow.meals.save.assert_called()


@pytest.mark.asyncio
async def test_timezone_and_initial_save_use_injected_uow():
    """get_user_timezone and meals.save are called on the injected UoW instance."""
    mock_meal = _make_meal_mock()
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals.save = AsyncMock(return_value=mock_meal)
    mock_uow.commit = AsyncMock()

    handler = _make_handler(mock_uow)
    cmd = UploadMealImageImmediatelyCommand(
        user_id=_FAKE_USER_UUID, file_contents=b"img", content_type="image/jpeg"
    )

    await handler.handle(cmd)

    # Both operations should be on the same injected UoW
    mock_uow.users.get_user_timezone.assert_called_once()
    mock_uow.meals.save.assert_called()
    mock_uow.meals.find_by_id.assert_not_called()
