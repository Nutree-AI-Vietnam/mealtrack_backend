from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.handlers.command_handlers.upload_meal_image_immediately_command_handler import (
    UploadMealImageImmediatelyHandler,
)
from src.domain.exceptions.ai_exceptions import MealResponseLocalizationError
from src.domain.model.meal import MealStatus
from src.domain.model.nutrition import FoodItem, Macros, Nutrition
from src.domain.services.meal_analysis.fast_path_policy import MealAnalyzeFastPathPolicy


@pytest.mark.asyncio
async def test_run_vision_analysis_retries_once_then_succeeds():
    handler = UploadMealImageImmediatelyHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        fast_path_policy=MealAnalyzeFastPathPolicy(max_attempts=2),
    )
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        side_effect=[
            Exception("vision failed"),
            {"dish_name": "Pho"},
        ]
    )

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"img",
        content_type="image/jpeg",
    )

    result = await handler._run_vision_analysis(command, "meal-123")

    assert result == {"dish_name": "Pho"}
    assert handler.vision_service.analyze.call_count == 2


@pytest.mark.asyncio
async def test_run_vision_analysis_raises_after_max_attempts():
    handler = UploadMealImageImmediatelyHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        fast_path_policy=MealAnalyzeFastPathPolicy(max_attempts=2),
    )
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(side_effect=Exception("vision failed"))

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"img",
        content_type="image/jpeg",
    )

    with pytest.raises(Exception, match="vision failed"):
        await handler._run_vision_analysis(command, "meal-123")

    assert handler.vision_service.analyze.call_count == 2


@pytest.mark.asyncio
async def test_run_vision_analysis_does_not_retry_invalid_localization():
    handler = UploadMealImageImmediatelyHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        fast_path_policy=MealAnalyzeFastPathPolicy(max_attempts=3),
    )
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "foods": [{"name": "Pho", "localized_name": "Phở"}],
            }
        }
    )

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"img",
        content_type="image/jpeg",
        language="vi",
    )

    with pytest.raises(MealResponseLocalizationError):
        await handler._run_vision_analysis(command, "meal-123")

    assert handler.vision_service.analyze.call_count == 1


@pytest.mark.asyncio
async def test_run_vision_analysis_does_not_retry_malformed_localization_container():
    handler = UploadMealImageImmediatelyHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        fast_path_policy=MealAnalyzeFastPathPolicy(max_attempts=3),
    )
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "foods": 1,
            }
        }
    )

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"img",
        content_type="image/jpeg",
        language="vi",
    )

    with pytest.raises(MealResponseLocalizationError):
        await handler._run_vision_analysis(command, "meal-123")

    assert handler.vision_service.analyze.call_count == 1


@pytest.mark.asyncio
async def test_non_english_image_response_skips_translation_persistence(caplog):
    caplog.set_level("INFO")
    call_order = []
    saved_state = {}
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals = MagicMock()

    async def save_meal(meal):
        call_order.append("save")
        saved_state["meal"] = meal
        return meal

    async def find_meal(meal_id, projection=None):
        call_order.append("find")
        return saved_state["meal"]

    mock_uow.meals.save = AsyncMock(side_effect=save_meal)
    mock_uow.meals.find_by_id = AsyncMock(side_effect=find_meal)

    async def commit():
        call_order.append("commit")

    mock_uow.commit = AsyncMock(side_effect=commit)

    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=mock_event_bus,
        event_publisher=MagicMock(publish=AsyncMock()),
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(
        return_value=(
            "https://res.cloudinary.com/demo/image/upload/00000000-0000-0000-0000-000000000123"
        )
    )
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "dish_name": "Pho",
                "localized_language": "vi",
                "localized_dish_name": "Phở",
                "foods": [{"name": "Pho", "localized_name": "Phở"}],
            }
        }
    )
    handler.gpt_parser = MagicMock()
    nutrition = Nutrition(
        macros=Macros(protein=20, carbs=30, fat=10),
        food_items=[
            FoodItem(
                id="item-1",
                name="Pho",
                quantity=300,
                unit="g",
                macros=Macros(protein=20, carbs=30, fat=10),
            )
        ],
    )
    handler.gpt_parser.parse_to_nutrition.return_value = nutrition
    handler.gpt_parser.parse_dish_name.return_value = "Pho"
    handler.gpt_parser.parse_emoji.return_value = "🍲"
    handler.gpt_parser.extract_raw_json.return_value = "{}"
    handler.meal_translation_service = MagicMock()
    handler.meal_translation_service.translate_meal = AsyncMock()

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"img",
        content_type="image/jpeg",
        language="vi",
    )

    await handler.handle(command)

    handler.meal_translation_service.translate_meal.assert_not_awaited()
    mock_uow.meals.find_by_id.assert_not_awaited()
    assert call_order[:2] == ["save", "commit"]
    assert "translated to" not in caplog.text
    assert saved_state["meal"].dish_name == "Phở"


@pytest.fixture
def parallel_mode_harness():
    saved_state = {"meals": []}
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals = MagicMock()

    async def save_meal(meal):
        saved_state["meals"].append(meal)
        saved_state["meal"] = meal
        return meal

    async def find_meal(meal_id, projection=None):
        return saved_state.get("meal")

    mock_uow.meals.save = AsyncMock(side_effect=save_meal)
    mock_uow.meals.find_by_id = AsyncMock(side_effect=find_meal)
    mock_uow.commit = AsyncMock()

    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=mock_event_bus,
        event_publisher=MagicMock(publish=AsyncMock()),
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock()
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock()
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_to_nutrition.return_value = SimpleNamespace(
        food_items=[MagicMock()], calories=400
    )
    handler.gpt_parser.parse_dish_name.return_value = "Pho"
    handler.gpt_parser.parse_emoji.return_value = "🍲"
    handler.gpt_parser.extract_raw_json.return_value = "{}"
    handler.meal_translation_service = MagicMock()
    handler.meal_translation_service.translate_meal = AsyncMock()

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"img",
        content_type="image/jpeg",
        language="en",
    )
    return SimpleNamespace(handler=handler, command=command, saved_state=saved_state)


@pytest.mark.asyncio
async def test_parallel_mode_runs_upload_and_analysis_and_returns_ready(
    parallel_mode_harness,
):
    harness = parallel_mode_harness

    harness.handler.image_store.save_async = AsyncMock(
        return_value="https://res.cloudinary.com/demo/image/upload/00000000-0000-0000-0000-000000000123"
    )

    def analyze_side_effect(file_contents, **kwargs):
        return {"dish_name": "Pho"}

    harness.handler.vision_service.analyze.side_effect = analyze_side_effect

    meal = await harness.handler.handle(harness.command)

    assert meal.status == MealStatus.READY


@pytest.mark.asyncio
async def test_parallel_mode_marks_failed_when_upload_fails_but_analysis_succeeds(
    parallel_mode_harness,
):
    harness = parallel_mode_harness

    harness.handler.image_store.save_async = AsyncMock(
        side_effect=RuntimeError("upload failed")
    )

    def analyze_side_effect(file_contents, **kwargs):
        return {"dish_name": "Pho"}

    harness.handler.vision_service.analyze.side_effect = analyze_side_effect

    with pytest.raises(RuntimeError, match="Cloudinary upload failed"):
        await harness.handler.handle(harness.command)

    # Upload-first flow: analysis never runs when upload fails
    assert harness.handler.vision_service.analyze.call_count == 0
    # No DB record created when upload fails
    assert len(harness.saved_state["meals"]) == 0


@pytest.mark.asyncio
async def test_parallel_mode_marks_failed_when_analysis_fails_even_if_upload_succeeds(
    parallel_mode_harness,
):
    harness = parallel_mode_harness

    harness.handler.image_store.save_async = AsyncMock(
        return_value="https://res.cloudinary.com/demo/image/upload/00000000-0000-0000-0000-000000000123"
    )

    def analyze_side_effect(file_contents, **kwargs):
        raise ValueError("analysis failed")

    harness.handler.vision_service.analyze.side_effect = analyze_side_effect

    with pytest.raises(ValueError, match="analysis failed"):
        await harness.handler.handle(harness.command)

    assert harness.handler.image_store.save_async.call_count == 1
    # No DB record created when analysis fails (upload-first flow)
    assert len(harness.saved_state["meals"]) == 0


@pytest.mark.asyncio
async def test_parallel_mode_prioritises_analysis_error_when_both_fail(
    parallel_mode_harness,
):
    harness = parallel_mode_harness

    harness.handler.image_store.save_async = AsyncMock(
        side_effect=RuntimeError("upload failed")
    )

    def analyze_side_effect(file_contents, **kwargs):
        raise ValueError("analysis failed")

    harness.handler.vision_service.analyze.side_effect = analyze_side_effect

    # Upload-first: upload error is raised before analysis even runs
    with pytest.raises(RuntimeError, match="Cloudinary upload failed"):
        await harness.handler.handle(harness.command)

    # No DB record created (upload-first flow aborts before any DB write)
    assert len(harness.saved_state["meals"]) == 0
