"""Tests for the default-off meal analysis graph scaffold."""

from unittest.mock import AsyncMock, patch

import pytest

from src.api.exceptions import ValidationException
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.graphs.meal_analyze.graph import (
    build_meal_analyze_graph,
    run_meal_analyze_graph,
    run_meal_analyze_graph_async,
)
from src.app.graphs.meal_analyze.nodes import (
    acquire_image,
    analyze_vision,
)
from src.app.graphs.meal_analyze.runtime import AcquiredImage, MealAnalyzeRuntime
from src.domain.exceptions.ai_exceptions import MealResponseLocalizationError
from src.domain.model.meal import MealStatus
from src.domain.model.meal.meal_response_localization import (
    parse_meal_response_localization,
)
from src.domain.parsers.vision_response_parser import VisionResponseParser
from src.infra.config.settings import Settings


class _FakeGraphUow:
    def __init__(self):
        self.users = AsyncMock()
        self.users.get_user_timezone = AsyncMock(return_value="UTC")
        self.meals = AsyncMock()
        self._saved_meals = []

        async def save_meal(meal):
            self._saved_meals.append(meal)
            return meal

        self.meals.save = AsyncMock(side_effect=save_meal)
        self.meals.find_by_id = AsyncMock(
            side_effect=lambda meal_id, **_: self._saved_meals[-1]
        )
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_meal_analyze_graph_settings_default_to_disabled(monkeypatch):
    monkeypatch.delenv("AI_MEAL_ANALYZE_GRAPH_ENABLED", raising=False)
    monkeypatch.delenv("AI_MEAL_ANALYZE_FATSECRET_VALIDATION_ENABLED", raising=False)
    monkeypatch.delenv(
        "AI_MEAL_ANALYZE_EXTERNAL_PROVIDER_TIMEOUT_SECONDS", raising=False
    )
    monkeypatch.delenv("AI_MEAL_ANALYZE_GRAPH_VERSION", raising=False)
    settings = Settings(_env_file=None)

    assert settings.AI_MEAL_ANALYZE_GRAPH_ENABLED is False
    assert settings.AI_MEAL_ANALYZE_FATSECRET_VALIDATION_ENABLED is False
    assert settings.AI_MEAL_ANALYZE_EXTERNAL_PROVIDER_TIMEOUT_SECONDS == 5.0
    assert settings.AI_MEAL_ANALYZE_GRAPH_VERSION == "v1"


def test_build_meal_analyze_graph_compiles():
    graph = build_meal_analyze_graph()

    assert hasattr(graph, "invoke")


def test_run_meal_analyze_graph_preserves_input_compatible_state():
    result = run_meal_analyze_graph(
        {
            "scan_mode": "food_label",
            "image_id": "image-123",
            "user_id": "user-123",
            "target_date": None,
        }
    )

    assert result["image_id"] == "image-123"
    assert result["user_id"] == "user-123"
    assert result["selected_mode"] == "food_label"
    assert result["graph_version"] == "v1"
    assert result["prepared"] is True
    assert result["completed"] is True


@pytest.mark.asyncio
async def test_async_graph_runner_executes_runtime_bound_acquisition():
    image_store = AsyncMock()
    image_store.save_async = AsyncMock(
        return_value="https://res.cloudinary.com/demo/image/upload/mealtrack/image-123.jpg"
    )
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="user-123",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
        ),
        image_store=image_store,
        image_id_factory=lambda: "image-123",
    )

    result = await run_meal_analyze_graph_async(
        {
            "scan_mode": "meal_scan",
            "user_id": "user-123",
            "target_date": None,
        },
        runtime,
    )

    assert result["image_id"] == "image-123"
    assert result["content_kind"] == "meal_image"
    assert result["image_size_bytes"] == len(b"upload-bytes")
    assert result["completed"] is True
    assert runtime.acquired_image is not None
    assert "image_url" not in result
    assert "image_bytes" not in result


@pytest.mark.asyncio
async def test_async_graph_runner_persists_ready_meal_and_invalidates_cache():
    image_id = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
    meal_id = "22222222-2222-4222-8222-222222222222"
    image_store = AsyncMock()
    image_store.save_async = AsyncMock(
        return_value=f"https://res.cloudinary.com/demo/image/upload/mealtrack/{image_id}.jpg"
    )
    vision_service = AsyncMock()
    vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Chicken rice",
                "confidence": 0.91,
                "foods": [
                    {
                        "name": "Chicken rice",
                        "quantity_g": 300,
                        "confidence": 0.91,
                        "macros": {
                            "protein_g": 28,
                            "carbs_g": 52,
                            "fat_g": 8,
                            "fiber_g": 2,
                            "sugar_g": 1,
                        },
                    }
                ],
            }
        }
    )
    uow = _FakeGraphUow()
    publisher = AsyncMock()
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="00000000-0000-0000-0000-000000000001",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
        ),
        image_store=image_store,
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        uow=uow,
        event_publisher=publisher,
        image_id_factory=lambda: image_id,
        meal_id_factory=lambda: meal_id,
    )

    result = await run_meal_analyze_graph_async(
        {
            "scan_mode": "meal_scan",
            "user_id": runtime.command.user_id,
            "target_date": None,
        },
        runtime,
    )

    vision_service.analyze.assert_awaited_once_with(
        b"upload-bytes",
        language="en",
    )
    uow.meals.save.assert_awaited_once()
    assert publisher.publish.await_count == 1
    meal = result["result"]
    assert meal.meal_id == meal_id
    assert meal.status == MealStatus.READY
    assert meal.dish_name == "Chicken rice"
    assert meal.image.image_id == image_id
    assert result["meal_id"] == meal_id
    assert "image_url" not in result
    assert "image_bytes" not in result


@pytest.mark.asyncio
async def test_async_graph_runner_publishes_insight_event_after_persist():
    image_id = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
    meal_id = "22222222-2222-4222-8222-222222222222"
    image_store = AsyncMock()
    image_store.save_async = AsyncMock(
        return_value=f"https://res.cloudinary.com/demo/image/upload/mealtrack/{image_id}.jpg"
    )
    vision_service = AsyncMock()
    vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Chicken rice",
                "confidence": 0.91,
                "foods": [
                    {
                        "name": "Chicken rice",
                        "quantity_g": 300,
                        "confidence": 0.91,
                        "macros": {
                            "protein_g": 28,
                            "carbs_g": 52,
                            "fat_g": 8,
                            "fiber_g": 2,
                            "sugar_g": 1,
                        },
                    }
                ],
            }
        }
    )
    uow = _FakeGraphUow()
    publisher = AsyncMock()

    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="00000000-0000-0000-0000-000000000001",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
        ),
        image_store=image_store,
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        uow=uow,
        event_publisher=publisher,
        image_id_factory=lambda: image_id,
        meal_id_factory=lambda: meal_id,
    )

    result = await run_meal_analyze_graph_async(
        {
            "scan_mode": "meal_scan",
            "user_id": runtime.command.user_id,
            "target_date": None,
        },
        runtime,
    )

    assert publisher.publish.await_count == 1
    assert result["cache_invalidated"] is True
    assert "meal_value_insight_scheduled" not in result


@pytest.mark.asyncio
async def test_graph_ready_response_does_not_run_value_insight_ai(caplog):
    image_id = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
    meal_id = "22222222-2222-4222-8222-222222222222"
    image_store = AsyncMock()
    image_store.save_async = AsyncMock(
        return_value=f"https://res.cloudinary.com/demo/image/upload/mealtrack/{image_id}.jpg"
    )
    vision_service = AsyncMock()
    vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Chicken rice",
                "confidence": 0.91,
                "foods": [
                    {
                        "name": "Chicken rice",
                        "quantity_g": 300,
                        "confidence": 0.91,
                        "macros": {
                            "protein_g": 28,
                            "carbs_g": 52,
                            "fat_g": 8,
                            "fiber_g": 2,
                            "sugar_g": 1,
                        },
                    }
                ],
            }
        }
    )

    publisher = AsyncMock()
    ai_manager = AsyncMock()
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="00000000-0000-0000-0000-000000000001",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
        ),
        image_store=image_store,
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        uow=_FakeGraphUow(),
        event_publisher=publisher,
        image_id_factory=lambda: image_id,
        meal_id_factory=lambda: meal_id,
    )

    result = await run_meal_analyze_graph_async(
        {
            "scan_mode": "meal_scan",
            "user_id": runtime.command.user_id,
            "target_date": None,
        },
        runtime,
    )

    assert result["result"].status == MealStatus.READY
    assert publisher.publish.await_count == 1
    ai_manager.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_graph_runner_no_food_does_not_persist():
    image_id = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
    image_store = AsyncMock()
    image_store.save_async = AsyncMock(
        return_value=f"https://res.cloudinary.com/demo/image/upload/mealtrack/{image_id}.jpg"
    )
    vision_service = AsyncMock()
    vision_service.analyze = AsyncMock(
        return_value={"structured_data": {"is_food": False, "foods": []}}
    )
    uow = _FakeGraphUow()
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="00000000-0000-0000-0000-000000000001",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
        ),
        image_store=image_store,
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        uow=uow,
        image_id_factory=lambda: image_id,
    )

    with pytest.raises(
        ValidationException, match="Image does not appear to contain food"
    ):
        await run_meal_analyze_graph_async(
            {
                "scan_mode": "meal_scan",
                "user_id": runtime.command.user_id,
                "target_date": None,
            },
            runtime,
        )

    uow.meals.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_graph_food_label_crop_persists_original_image_reference():
    full_image_id = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
    crop_image_id = "33333333-3333-4333-8333-333333333333"
    full_url = (
        f"https://res.cloudinary.com/demo/image/upload/mealtrack/{full_image_id}.jpg"
    )
    crop_url = (
        f"https://res.cloudinary.com/demo/image/upload/mealtrack/{crop_image_id}.jpg"
    )
    download_image_bytes = AsyncMock(return_value=b"crop-label-bytes")
    vision_service = AsyncMock()
    vision_service.analyze_with_strategy = AsyncMock(
        return_value={
            "structured_data": {
                "is_food_label": True,
                "product_name": "Protein Bar",
                "brand": None,
                "serving_size": {"display_text": "50g", "grams": 50},
                "servings_per_package": 1,
                "label_calories_per_serving": 180,
                "macros_per_serving": {
                    "protein_g": 12,
                    "carbs_g": 20,
                    "fat_g": 6,
                    "fiber_g": 3,
                    "sugar_g": 8,
                },
                "confidence": 0.88,
                "label_notes": [],
            }
        }
    )
    uow = _FakeGraphUow()
    runtime = MealAnalyzeRuntime(
        command=ScanByUrlCommand(
            user_id="00000000-0000-0000-0000-000000000001",
            image_url=full_url,
            public_id=f"mealtrack/{full_image_id}",
            scan_mode="food_label",
            label_crop_image_url=crop_url,
            label_crop_public_id=f"mealtrack/{crop_image_id}",
        ),
        download_image_bytes=download_image_bytes,
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        uow=uow,
        meal_id_factory=lambda: "22222222-2222-4222-8222-222222222222",
    )

    result = await run_meal_analyze_graph_async(
        {
            "scan_mode": "food_label",
            "image_id": full_image_id,
            "user_id": runtime.command.user_id,
            "target_date": None,
        },
        runtime,
    )

    download_image_bytes.assert_awaited_once_with(crop_url)
    vision_service.analyze_with_strategy.assert_awaited_once()
    meal = result["result"]
    assert meal.image.image_id == full_image_id
    assert meal.image.url == full_url
    assert meal.source == "food_label"


@pytest.mark.asyncio
async def test_async_graph_returns_same_call_locale_without_translation_reload():
    image_id = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
    image_store = AsyncMock()
    image_store.save_async = AsyncMock(
        return_value=f"https://res.cloudinary.com/demo/image/upload/mealtrack/{image_id}.jpg"
    )
    vision_service = AsyncMock()
    vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Chicken rice",
                "localized_language": "vi",
                "localized_dish_name": "Cơm gà",
                "confidence": 0.91,
                "foods": [
                    {
                        "name": "Chicken rice",
                        "localized_name": "Cơm gà",
                        "quantity_g": 300,
                        "confidence": 0.91,
                        "macros": {
                            "protein_g": 28,
                            "carbs_g": 52,
                            "fat_g": 8,
                            "fiber_g": 2,
                            "sugar_g": 1,
                        },
                    }
                ],
            }
        }
    )
    uow = _FakeGraphUow()
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="00000000-0000-0000-0000-000000000001",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
            language="vi",
        ),
        image_store=image_store,
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        uow=uow,
        image_id_factory=lambda: image_id,
        meal_id_factory=lambda: "22222222-2222-4222-8222-222222222222",
    )

    with patch(
        "src.app.graphs.meal_analyze.nodes.parse_meal_response_localization",
        wraps=parse_meal_response_localization,
    ) as parse_localization:
        result = await run_meal_analyze_graph_async(
            {
                "scan_mode": "meal_scan",
                "user_id": runtime.command.user_id,
                "target_date": None,
            },
            runtime,
        )

    vision_service.analyze.assert_awaited_once_with(
        b"upload-bytes",
        language="vi",
    )
    assert uow.meals.find_by_id.await_count == 0
    assert result["result"].meal_id == "22222222-2222-4222-8222-222222222222"
    assert result["result"].dish_name == "Cơm gà"
    assert result["result"].nutrition.food_items[0].name == "Cơm gà"
    parse_localization.assert_called_once()


@pytest.mark.asyncio
async def test_async_graph_does_not_retry_invalid_localization():
    vision_service = AsyncMock()
    vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "foods": [{"name": "Pho", "localized_name": "Phở"}],
            }
        }
    )
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="user-123",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
            language="vi",
        ),
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        max_vision_attempts=3,
    )
    runtime.acquired_image = AcquiredImage(
        image_id="image-123",
        image_url="https://example.com/image-123.jpg",
        persisted_image_id="image-123",
        persisted_image_url="https://example.com/image-123.jpg",
        source_bytes=b"upload-bytes",
        analysis_bytes=b"upload-bytes",
        content_type="image/jpeg",
        content_kind="meal_image",
    )

    with pytest.raises(MealResponseLocalizationError):
        await analyze_vision({}, runtime)

    assert vision_service.analyze.await_count == 1


@pytest.mark.asyncio
async def test_async_graph_treats_malformed_localization_container_as_non_retryable():
    vision_service = AsyncMock()
    vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "foods": 1,
            }
        }
    )
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="user-123",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
            language="vi",
        ),
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        max_vision_attempts=3,
    )
    runtime.acquired_image = AcquiredImage(
        image_id="image-123",
        image_url="https://example.com/image-123.jpg",
        persisted_image_id="image-123",
        persisted_image_url="https://example.com/image-123.jpg",
        source_bytes=b"upload-bytes",
        analysis_bytes=b"upload-bytes",
        content_type="image/jpeg",
        content_kind="meal_image",
    )

    with pytest.raises(MealResponseLocalizationError):
        await analyze_vision({}, runtime)

    assert vision_service.analyze.await_count == 1


@pytest.mark.asyncio
async def test_async_graph_upload_vision_retries_transient_failure():
    image_id = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
    image_store = AsyncMock()
    image_store.save_async = AsyncMock(
        return_value=f"https://res.cloudinary.com/demo/image/upload/mealtrack/{image_id}.jpg"
    )
    vision_service = AsyncMock()
    vision_service.analyze = AsyncMock(
        side_effect=[
            RuntimeError("temporary vision outage"),
            {
                "structured_data": {
                    "is_food": True,
                    "dish_name": "Chicken rice",
                    "confidence": 0.91,
                    "foods": [
                        {
                            "name": "Chicken rice",
                            "quantity_g": 300,
                            "confidence": 0.91,
                            "macros": {
                                "protein_g": 28,
                                "carbs_g": 52,
                                "fat_g": 8,
                                "fiber_g": 2,
                                "sugar_g": 1,
                            },
                        }
                    ],
                }
            },
        ]
    )
    uow = _FakeGraphUow()
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="00000000-0000-0000-0000-000000000001",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
        ),
        image_store=image_store,
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        uow=uow,
        image_id_factory=lambda: image_id,
        meal_id_factory=lambda: "22222222-2222-4222-8222-222222222222",
        max_vision_attempts=2,
    )

    result = await run_meal_analyze_graph_async(
        {
            "scan_mode": "meal_scan",
            "user_id": runtime.command.user_id,
            "target_date": None,
        },
        runtime,
    )

    assert vision_service.analyze.await_count == 2
    assert result["result"].meal_id == "22222222-2222-4222-8222-222222222222"


@pytest.mark.asyncio
async def test_acquire_image_upload_saves_bytes_in_runtime_not_state():
    image_store = AsyncMock()
    image_store.save_async = AsyncMock(
        return_value="https://res.cloudinary.com/demo/image/upload/mealtrack/image-123.jpg"
    )
    runtime = MealAnalyzeRuntime(
        command=UploadMealImageImmediatelyCommand(
            user_id="user-123",
            file_contents=b"upload-bytes",
            content_type="image/jpeg",
        ),
        image_store=image_store,
        image_id_factory=lambda: "image-123",
    )

    state_update = await acquire_image({}, runtime)

    image_store.save_async.assert_awaited_once_with(
        b"upload-bytes",
        "image/jpeg",
        "image-123",
    )
    assert state_update == {
        "image_id": "image-123",
        "content_kind": "meal_image",
        "image_size_bytes": len(b"upload-bytes"),
    }
    assert runtime.acquired_image is not None
    assert runtime.acquired_image.image_url.startswith("https://")
    assert runtime.acquired_image.analysis_bytes == b"upload-bytes"
    assert "image_url" not in state_update
    assert "image_bytes" not in state_update


@pytest.mark.asyncio
async def test_acquire_image_scan_by_url_downloads_and_compresses_regular_scan():
    large_raw = b"x" * 300_000
    download_image_bytes = AsyncMock(return_value=large_raw)
    compression_calls = []

    def compress_image(raw_bytes: bytes) -> bytes:
        compression_calls.append(raw_bytes)
        return b"compressed-image"

    command = ScanByUrlCommand(
        user_id="user-123",
        image_url="https://res.cloudinary.com/demo/image/upload/v1/mealtrack/image-456.jpg",
        public_id="mealtrack/image-456",
        scan_mode="scanner",
    )
    runtime = MealAnalyzeRuntime(
        command=command,
        download_image_bytes=download_image_bytes,
        compress_image=compress_image,
    )

    state_update = await acquire_image({}, runtime)

    download_image_bytes.assert_awaited_once_with(
        "https://res.cloudinary.com/demo/image/upload/w_768,c_limit,q_auto,f_jpg/v1/mealtrack/image-456.jpg"
    )
    assert compression_calls == [large_raw]
    assert state_update == {
        "image_id": "image-456",
        "content_kind": "meal_image",
        "image_size_bytes": len(large_raw),
    }
    assert runtime.acquired_image is not None
    assert runtime.acquired_image.image_url == command.image_url
    assert runtime.acquired_image.analysis_bytes == b"compressed-image"
    assert "image_url" not in state_update
    assert "image_bytes" not in state_update


@pytest.mark.asyncio
async def test_acquire_image_food_label_prefers_crop_without_compression():
    full_url = "https://res.cloudinary.com/demo/image/upload/v1/mealtrack/full.jpg"
    crop_url = "https://res.cloudinary.com/demo/image/upload/v1/mealtrack/crop.jpg"
    download_image_bytes = AsyncMock(
        side_effect=lambda url: {
            full_url: b"full-label-bytes",
            crop_url: b"crop-label-bytes",
        }[url]
    )

    command = ScanByUrlCommand(
        user_id="user-123",
        image_url=full_url,
        public_id="mealtrack/full",
        scan_mode="food_label",
        label_crop_image_url=crop_url,
        label_crop_public_id="mealtrack/crop",
        crop_metadata={"crop_strategy": "food_label_visible_frame_v1"},
    )
    runtime = MealAnalyzeRuntime(
        command=command,
        download_image_bytes=download_image_bytes,
        compress_image=lambda raw_bytes: b"should-not-run",
    )

    state_update = await acquire_image({}, runtime)

    download_image_bytes.assert_awaited_once_with(crop_url)
    assert state_update == {
        "image_id": "crop",
        "content_kind": "food_label_image",
        "image_size_bytes": len(b"crop-label-bytes"),
    }
    assert runtime.acquired_image is not None
    assert runtime.acquired_image.image_url == crop_url
    assert runtime.acquired_image.analysis_bytes == b"crop-label-bytes"
    assert "image_url" not in state_update
    assert "image_bytes" not in state_update
