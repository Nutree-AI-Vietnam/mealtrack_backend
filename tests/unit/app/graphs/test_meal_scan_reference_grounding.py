"""Grounding flag selects the cascade. Off leaves the current scan path alone."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.graphs.meal_analyze import nodes
from src.app.graphs.meal_analyze.runtime import AcquiredImage, MealAnalyzeRuntime
from src.app.services.food_reference_validation_service import ValidationReport
from src.domain.parsers.vision_response_parser import VisionResponseParser

USER_ID = "11111111-2222-4333-8444-555555555555"
MEAL_ID = "00000000-0000-0000-0000-000000000301"
IMAGE_ID = "00000000-0000-0000-0000-000000000302"
IMAGE_URL = "https://images.example.test/private/scan.jpg"


def _food(name: str) -> dict:
    return {
        "name": name,
        "quantity_g": 150,
        "confidence": 0.9,
        "macros": {
            "protein_g": 4,
            "carbs_g": 45,
            "fat_g": 1,
            "fiber_g": 0,
            "sugar_g": 0,
        },
    }


def _runtime(*, validation_service=None) -> MealAnalyzeRuntime:
    parser = VisionResponseParser()
    vision = {
        "structured_data": {
            "is_food": True,
            "dish_name": "Rice",
            "confidence": 0.9,
            "foods": [_food("White Rice")],
        }
    }
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.meals.save = AsyncMock(side_effect=lambda meal: meal)
    uow.commit = AsyncMock()
    return MealAnalyzeRuntime(
        command=ScanByUrlCommand(
            user_id=USER_ID,
            image_url=IMAGE_URL,
            public_id="mealtrack/private-scan",
            scan_mode="scanner",
        ),
        acquired_image=AcquiredImage(
            image_id=IMAGE_ID,
            image_url=IMAGE_URL,
            persisted_image_id=IMAGE_ID,
            persisted_image_url=IMAGE_URL,
            source_bytes=b"img",
            analysis_bytes=b"img",
            content_type="image/jpeg",
            content_kind="photo",
        ),
        vision_result=vision,
        nutrition=parser.parse_to_nutrition(vision),
        gpt_parser=parser,
        uow=uow,
        event_publisher=MagicMock(),
        event_bus=MagicMock(),
        meal_id_factory=lambda: MEAL_ID,
        food_reference_validation_service=validation_service,
    )


async def _persist(runtime: MealAnalyzeRuntime, state: dict):
    with patch.object(nodes, "publish_meal_event", new=AsyncMock()):
        return await nodes.persist_meal(state, runtime)


@pytest.mark.asyncio
async def test_grounding_off_does_not_call_ground_meal():
    service = MagicMock()
    service.ground_meal = AsyncMock()
    service.validate_meal = AsyncMock()
    runtime = _runtime(validation_service=service)
    assert runtime.reference_grounding_enabled is False

    state = await nodes.maybe_validate_reference({}, runtime)
    await _persist(runtime, state)

    assert state == {"reference_validated": False}
    service.ground_meal.assert_not_awaited()
    service.validate_meal.assert_not_awaited()
    saved = runtime.saved_meal
    assert saved is not None
    assert saved.nutrition.food_items[0].macros.protein == 4
    assert saved.nutrition.food_items[0].source_kind is None


@pytest.mark.asyncio
async def test_grounding_on_uses_cascade_report_not_closeness_audit():
    async def ground_meal(meal):
        item = meal.nutrition.food_items[0]
        item.macros.protein = 4.05
        item.macros.carbs = 42.0
        item.macros.fat = 0.45
        item.source_kind = "local"
        meal.nutrition.macros.protein = 4.05
        meal.nutrition.macros.carbs = 42.0
        meal.nutrition.macros.fat = 0.45
        return ValidationReport(meal=meal, outcome="match")

    service = MagicMock()
    service.ground_meal = AsyncMock(side_effect=ground_meal)
    service.validate_meal = AsyncMock()
    runtime = _runtime(validation_service=service)
    runtime.reference_grounding_enabled = True
    runtime.fatsecret_validation_enabled = True

    state = await nodes.maybe_validate_reference({}, runtime)
    await _persist(runtime, state)

    assert state == {"reference_grounding_pending": True}
    service.ground_meal.assert_awaited_once()
    service.validate_meal.assert_not_awaited()
    saved = runtime.saved_meal
    assert saved is not None
    assert saved.nutrition.food_items[0].macros.protein == pytest.approx(4.05)
    assert saved.nutrition.food_items[0].source_kind == "local"
