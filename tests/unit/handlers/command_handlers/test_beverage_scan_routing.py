"""Unit tests for beverage scan behavior in UploadMealImageImmediatelyHandler.

Verifies that packaged beverage images now follow the standard meal path.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.exceptions import ValidationException
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.handlers.command_handlers.upload_meal_image_immediately_command_handler import (
    UploadMealImageImmediatelyHandler,
)
from src.domain.parsers.vision_response_parser import VisionResponseParser

_CLOUDINARY_URL = "https://res.cloudinary.com/test/image/upload/v1/mealtrack/drink.jpg"
_USER_ID = "00000000-0000-0000-0000-000000000001"


def _make_uow(*, save_return=None):
    """Build a minimal async-context-manager UoW mock."""
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals = MagicMock()
    mock_uow.meals.find_by_id = AsyncMock(side_effect=lambda mid, **kw: save_return)

    saved_meals = []

    async def capture_meal(meal):
        saved_meals.append(meal)
        return meal

    mock_uow.meals.save = AsyncMock(side_effect=capture_meal)
    mock_uow.hydration_entries = MagicMock()
    mock_uow.hydration_entries.add = AsyncMock(side_effect=lambda e: e)
    mock_uow.commit = AsyncMock()
    mock_uow._saved_meals = saved_meals
    return mock_uow


def _make_command(user_id: str = _USER_ID) -> UploadMealImageImmediatelyCommand:
    return UploadMealImageImmediatelyCommand(
        user_id=user_id,
        file_contents=b"fake-image-bytes",
        content_type="image/jpeg",
    )


@pytest.mark.asyncio
async def test_packaged_beverage_scan_creates_standard_meal_not_hydration_entry():
    """Packaged beverages scanned through meal scan should persist as meals."""
    mock_uow = _make_uow()
    mock_uow.meals.find_by_id = AsyncMock(
        side_effect=lambda mid, **kw: mock_uow._saved_meals[-1]
    )
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=MagicMock(),
        event_publisher=publisher,
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(return_value=_CLOUDINARY_URL)

    bev_analysis = {
        "structured_data": {
            "is_food": True,
            "dish_name": "Coca-Cola",
            "foods": [
                {
                    "name": "Coca-Cola",
                    "quantity_g": 330,
                    "macros": {
                        "protein_g": 0,
                        "carbs_g": 35,
                        "fat_g": 0,
                        "fiber_g": 0,
                        "sugar_g": 35,
                    },
                }
            ],
            "confidence": 0.95,
            "beverage_metadata": None,
        }
    }
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(return_value=bev_analysis)
    handler.gpt_parser = VisionResponseParser()

    result = await handler.handle(_make_command())

    mock_uow.hydration_entries.add.assert_not_called()
    assert len(mock_uow._saved_meals) == 1
    assert mock_uow._saved_meals[0].source == "scanner"
    assert mock_uow._saved_meals[0].dish_name == "Coca-Cola"
    assert [call.args[0]["event_type"] for call in publisher.publish.await_args_list] == [
        "meal.created.v1",
    ]
    assert result.meal_id == mock_uow._saved_meals[0].meal_id


@pytest.mark.asyncio
async def test_food_scan_unchanged_path():
    """Non-beverage food scans follow the standard meal creation path."""
    mock_uow = _make_uow()
    mock_uow.meals.find_by_id = AsyncMock(
        side_effect=lambda mid, **kw: mock_uow._saved_meals[-1]
    )
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=MagicMock(),
        event_publisher=publisher,
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(return_value=_CLOUDINARY_URL)

    # Standard food analysis — no beverage_metadata
    food_analysis = {
        "structured_data": {
            "is_food": True,
            "dish_name": "Pho",
            "foods": [{"name": "Pho", "quantity_g": 500}],
            "confidence": 0.95,
        }
    }
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(return_value=food_analysis)

    nutrition = SimpleNamespace(
        food_items=[SimpleNamespace(name="Pho")],
        calories=450,
        macros=SimpleNamespace(protein=20, carbs=60, fat=10, fiber=2, sugar=5),
    )
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_is_food.return_value = True
    handler.gpt_parser.parse_to_nutrition.return_value = nutrition
    handler.gpt_parser.parse_dish_name.return_value = "Pho"
    handler.gpt_parser.parse_emoji.return_value = "🍜"
    handler.gpt_parser.extract_raw_json.return_value = "{}"

    await handler.handle(_make_command())

    # Standard meal path: no hydration entry
    mock_uow.hydration_entries.add.assert_not_called()

    # Meal saved with source="scanner"
    assert len(mock_uow._saved_meals) >= 1
    assert mock_uow._saved_meals[0].source == "scanner"

    # Meal event published
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_food_scan_with_beverage_metadata_false_follows_food_path():
    """is_packaged_beverage=False on beverage_metadata still routes to food path."""
    mock_uow = _make_uow()
    mock_uow.meals.find_by_id = AsyncMock(
        side_effect=lambda mid, **kw: mock_uow._saved_meals[-1]
    )
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=MagicMock(),
        event_publisher=publisher,
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(return_value=_CLOUDINARY_URL)

    food_analysis = {
        "structured_data": {
            "is_food": True,
            "dish_name": "Smoothie bowl",
            "foods": [{"name": "Smoothie bowl", "quantity_g": 300}],
            "confidence": 0.85,
            "beverage_metadata": {
                "is_packaged_beverage": False,  # not packaged
            },
        }
    }
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(return_value=food_analysis)

    nutrition = SimpleNamespace(
        food_items=[SimpleNamespace(name="Smoothie bowl")],
        calories=300,
        macros=SimpleNamespace(protein=5, carbs=50, fat=8, fiber=3, sugar=20),
    )
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_is_food.return_value = True
    handler.gpt_parser.parse_to_nutrition.return_value = nutrition
    handler.gpt_parser.parse_dish_name.return_value = "Smoothie bowl"
    handler.gpt_parser.parse_emoji.return_value = "🥣"
    handler.gpt_parser.extract_raw_json.return_value = "{}"

    await handler.handle(_make_command())

    mock_uow.hydration_entries.add.assert_not_called()
    publisher.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_zero_calorie_drink_does_not_create_hydration_entry_from_meal_scan():
    """Zero-calorie drinks should not be silently routed to hydration from meal scan."""
    mock_uow = _make_uow()
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=MagicMock(),
        event_publisher=publisher,
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(return_value=_CLOUDINARY_URL)
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Water bottle",
                "foods": [
                    {
                        "name": "water",
                        "quantity_g": 500,
                        "macros": {
                            "protein_g": 0,
                            "carbs_g": 0,
                            "fat_g": 0,
                            "fiber_g": 0,
                            "sugar_g": 0,
                        },
                    }
                ],
                "confidence": 0.9,
                "beverage_metadata": None,
            }
        }
    )

    nutrition = SimpleNamespace(
        food_items=[SimpleNamespace(name="water")],
        calories=0,
        macros=SimpleNamespace(protein=0, carbs=0, fat=0, fiber=0, sugar=0),
    )
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_is_food.return_value = True
    handler.gpt_parser.parse_to_nutrition.return_value = nutrition
    handler.gpt_parser.parse_dish_name.return_value = "Water bottle"

    with pytest.raises(ValidationException, match="No edible food detected"):
        await handler.handle(_make_command())

    mock_uow.hydration_entries.add.assert_not_called()
    mock_uow.meals.save.assert_not_called()
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_scan_captures_rejected_image_for_review(monkeypatch):
    """Expected non-food 400s still create a reviewable Sentry message with image URL."""
    from src.app.handlers.command_handlers import (
        upload_meal_image_immediately_command_handler as module,
    )

    capture_message = MagicMock()
    monkeypatch.setattr(module, "capture_message", capture_message)

    mock_uow = _make_uow()
    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=MagicMock(),
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(return_value=_CLOUDINARY_URL)
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={"structured_data": {"is_food": False, "foods": []}}
    )
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_is_food.return_value = False

    with pytest.raises(
        ValidationException, match="Image does not appear to contain food"
    ):
        await handler.handle(_make_command())

    capture_message.assert_called_once()
    _, kwargs = capture_message.call_args
    assert kwargs["level"] == "warning"
    assert kwargs["context"]["component"] == "meal_scan"
    assert kwargs["context"]["operation"] == "upload_meal_image_immediate"
    assert kwargs["context"]["image_url"] == _CLOUDINARY_URL
    assert kwargs["context"]["rejection_reason"] == "parser_not_food"
    assert kwargs["context"]["image_id"]
