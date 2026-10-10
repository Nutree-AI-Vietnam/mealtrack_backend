"""Tests for image-database consistency in upload handler."""

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
from src.domain.model.meal import MealStatus
from src.domain.parsers.vision_response_parser import VisionResponseParser


@pytest.mark.asyncio
async def test_upload_failure_does_not_create_db_record():
    """When Cloudinary upload fails, no meal record should be created in DB."""
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals = MagicMock()
    mock_uow.meals.save = AsyncMock()
    mock_uow.commit = AsyncMock()

    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=mock_event_bus,
        event_publisher=MagicMock(publish=AsyncMock()),
    )

    # Cloudinary upload fails
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(
        side_effect=Exception("Cloudinary upload failed")
    )

    handler.vision_service = MagicMock()
    handler.gpt_parser = MagicMock()

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"fake-image-bytes",
        content_type="image/jpeg",
    )

    with pytest.raises(Exception, match="Cloudinary upload failed"):
        await handler.handle(command)

    # Key assertion: meals.save should NOT have been called
    mock_uow.meals.save.assert_not_called()
    mock_uow.commit.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_cloudinary_url_does_not_create_db_record_or_log_url(caplog):
    """When Cloudinary returns invalid URL, no meal record should be created."""
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals = MagicMock()
    mock_uow.meals.save = AsyncMock()
    mock_uow.commit = AsyncMock()

    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=mock_event_bus,
    )

    # Cloudinary returns invalid URL (just the image_id, not a URL)
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(return_value="just-an-id-not-a-url")

    handler.vision_service = MagicMock()
    handler.gpt_parser = MagicMock()

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"fake-image-bytes",
        content_type="image/jpeg",
    )

    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError, match="Cloudinary upload failed"):
            await handler.handle(command)

    mock_uow.meals.save.assert_not_called()
    mock_uow.commit.assert_not_called()
    assert "just-an-id-not-a-url" not in caplog.text


@pytest.mark.asyncio
async def test_non_food_upload_rejects_before_db_record():
    """Non-food vision output should not create a meal or parse nutrition."""
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals = MagicMock()
    mock_uow.meals.save = AsyncMock()
    mock_uow.commit = AsyncMock()

    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=mock_event_bus,
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(
        return_value="https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc123.jpg"
    )
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={"structured_data": {"is_food": False, "foods": []}}
    )
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_is_food.return_value = False

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"fake-image-bytes",
        content_type="image/jpeg",
    )

    with pytest.raises(
        ValidationException, match="Image does not appear to contain food"
    ):
        await handler.handle(command)

    handler.gpt_parser.parse_to_nutrition.assert_not_called()
    mock_uow.meals.save.assert_not_called()
    mock_uow.commit.assert_not_called()


@pytest.mark.asyncio
async def test_successful_upload_creates_meal_with_verified_url():
    """When upload succeeds with valid URL, meal is created with that URL."""
    saved_meals = []

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals = MagicMock()

    async def capture_save(meal):
        saved_meals.append(meal)
        return meal

    mock_uow.meals.save = AsyncMock(side_effect=capture_save)
    mock_uow.meals.find_by_id = AsyncMock(side_effect=lambda mid, **kw: saved_meals[-1])
    mock_uow.commit = AsyncMock()

    mock_event_bus = MagicMock()
    mock_event_bus.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=mock_event_bus,
        event_publisher=MagicMock(publish=AsyncMock()),
    )

    # Cloudinary returns valid URL
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(
        return_value=(
            "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc123.jpg"
        )
    )

    # Vision service returns valid analysis
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(return_value={"dish_name": "Test Dish"})

    # Parser returns valid nutrition
    handler.gpt_parser = MagicMock()
    nutrition = SimpleNamespace(
        food_items=[SimpleNamespace(name="item1")],
        calories=400,
    )
    handler.gpt_parser.parse_to_nutrition.return_value = nutrition
    handler.gpt_parser.parse_dish_name.return_value = "Test Dish"
    handler.gpt_parser.parse_emoji.return_value = None
    handler.gpt_parser.extract_raw_json.return_value = "{}"

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"fake-image-bytes",
        content_type="image/jpeg",
    )

    await handler.handle(command)

    # Verify meal was saved with the Cloudinary URL
    assert len(saved_meals) > 0
    final_meal = saved_meals[-1]
    assert (
        final_meal.image.url
        == "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc123.jpg"
    )
    assert final_meal.status.value == "READY"


@pytest.mark.asyncio
async def test_successful_upload_keeps_ready_scanner_contract_with_backend_calories():
    """Direct image analyze returns a READY scanner meal with backend-derived calories."""
    saved_meals = []
    cloudinary_url = (
        "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc123.jpg"
    )

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    mock_uow.meals = MagicMock()

    async def capture_save(meal):
        saved_meals.append(meal)
        return meal

    mock_uow.meals.save = AsyncMock(side_effect=capture_save)
    mock_uow.meals.find_by_id = AsyncMock(side_effect=lambda mid, **kw: saved_meals[-1])
    mock_uow.commit = AsyncMock()

    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = UploadMealImageImmediatelyHandler(
        uow=mock_uow,
        event_bus=MagicMock(),
        event_publisher=publisher,
    )
    handler.image_store = MagicMock()
    handler.image_store.save_async = AsyncMock(return_value=cloudinary_url)
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Chicken rice",
                "foods": [
                    {
                        "name": "Chicken rice",
                        "quantity_g": 300,
                        "macros": {
                            "protein_g": 10,
                            "carbs_g": 50,
                            "fat_g": 8,
                            "fiber_g": 5,
                            "sugar_g": 2,
                        },
                    }
                ],
                "confidence": 0.9,
            }
        }
    )
    handler.gpt_parser = VisionResponseParser()

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"fake-image-bytes",
        content_type="image/jpeg",
    )

    result = await handler.handle(command)

    assert len(saved_meals) == 1
    saved_meal = saved_meals[0]
    assert result.meal_id == saved_meal.meal_id
    assert saved_meal.status == MealStatus.READY
    assert saved_meal.source == "scanner"
    assert saved_meal.image.url == cloudinary_url
    assert saved_meal.image.size_bytes == len(command.file_contents)
    assert saved_meal.nutrition.calories == pytest.approx(302.0)
    mock_uow.meals.save.assert_awaited_once()
    assert [
        call.args[0]["event_type"] for call in publisher.publish.await_args_list
    ] == [
        "meal.created.v1",
    ]


@pytest.mark.asyncio
async def test_upload_uses_workflow_only_when_graph_enabled():
    expected_meal = object()
    workflow = MagicMock()
    workflow.run_uploaded = AsyncMock(return_value=expected_meal)
    handler = UploadMealImageImmediatelyHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        image_store=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=MagicMock(),
        meal_analyze_workflow=workflow,
        meal_analyze_graph_enabled=True,
    )
    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"fake-image-bytes",
        content_type="image/jpeg",
    )

    result = await handler.handle(command)

    assert result is expected_meal
    workflow.run_uploaded.assert_awaited_once()
    assert workflow.run_uploaded.await_args.args[0] == command


@pytest.mark.asyncio
async def test_upload_graph_disabled_keeps_legacy_path():
    workflow = MagicMock()
    workflow.run_uploaded = AsyncMock()
    handler = UploadMealImageImmediatelyHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        image_store=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=MagicMock(),
        meal_analyze_workflow=workflow,
        meal_analyze_graph_enabled=False,
    )
    handler._handle_parallel_upload = AsyncMock(return_value="legacy-result")
    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"fake-image-bytes",
        content_type="image/jpeg",
    )

    result = await handler.handle(command)

    assert result == "legacy-result"
    handler._handle_parallel_upload.assert_awaited_once_with(command)
    workflow.run_uploaded.assert_not_awaited()
