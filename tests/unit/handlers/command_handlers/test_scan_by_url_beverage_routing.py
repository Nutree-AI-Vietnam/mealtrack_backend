"""Unit tests for scan-by-url beverage meal behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.exceptions import ValidationException
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.handlers.command_handlers.scan_by_url_command_handler import (
    ScanByUrlCommandHandler,
)
from src.domain.parsers.vision_response_parser import VisionResponseParser

_IMAGE_URL = "https://res.cloudinary.com/test/image/upload/v1/mealtrack/drink.jpg"
_PUBLIC_ID = "mealtrack/1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
_USER_ID = "00000000-0000-0000-0000-000000000001"


def _make_uow() -> MagicMock:
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users = MagicMock()
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.meals = MagicMock()
    saved_meals = []

    async def capture_meal(meal):
        saved_meals.append(meal)
        return meal

    uow.meals.save = AsyncMock(side_effect=capture_meal)
    uow.meals.find_by_id = AsyncMock(side_effect=lambda mid, **kw: saved_meals[-1])
    uow.hydration_entries = MagicMock()
    uow.hydration_entries.add = AsyncMock(side_effect=lambda entry: entry)
    uow.commit = AsyncMock()
    uow._saved_meals = saved_meals
    return uow


def _install_fake_image_download(
    monkeypatch,
    responses: dict[str, bytes] | None = None,
) -> None:
    from src.app.handlers.command_handlers import scan_by_url_command_handler as module

    class FakeResponse:
        def __init__(self, content: bytes):
            self.content = content

        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *args, **kwargs):
            content = (
                responses.get(url, b"fake-image-bytes")
                if responses
                else b"fake-image-bytes"
            )
            return FakeResponse(content)

    import httpx

    fake_client = FakeClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: fake_client)
    monkeypatch.setattr(module, "compress_image", lambda raw_bytes: raw_bytes)


@pytest.mark.asyncio
async def test_scan_by_url_packaged_beverage_creates_standard_meal(monkeypatch):
    _install_fake_image_download(monkeypatch)
    uow = _make_uow()
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=MagicMock(),
        event_publisher=publisher,
    )
    handler.vision_service.analyze = AsyncMock(
        return_value={
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
                "beverage_metadata": None,
            }
        }
    )
    handler.gpt_parser = VisionResponseParser()

    result = await handler.handle(
        ScanByUrlCommand(
            user_id=_USER_ID,
            image_url=_IMAGE_URL,
            public_id=_PUBLIC_ID,
        )
    )

    uow.hydration_entries.add.assert_not_called()
    uow.meals.save.assert_awaited_once()
    assert [
        call.args[0]["event_type"] for call in publisher.publish.await_args_list
    ] == [
        "meal.created.v1",
    ]

    assert result.meal_id == uow._saved_meals[0].meal_id
    assert result.source == "scanner"
    assert result.image.url == _IMAGE_URL


@pytest.mark.asyncio
async def test_scan_by_url_food_label_uses_crop_image_ai_without_ocr(
    monkeypatch,
):
    crop_url = "https://res.cloudinary.com/test/image/upload/v1/mealtrack/crop.png"
    _install_fake_image_download(
        monkeypatch,
        responses={
            _IMAGE_URL: b"full-image-bytes",
            crop_url: b"label-crop-image-bytes",
        },
    )
    uow = _make_uow()
    cache = MagicMock()
    cache.enqueue_meal_invalidation = AsyncMock()
    vision_service = MagicMock()
    vision_service.analyze_with_strategy = AsyncMock(
        return_value={
            "structured_data": {
                "is_food_label": True,
                "product_name": "Danu Ensan Malaysia",
                "brand": None,
                "serving_size": {"display_text": "100g", "grams": 100},
                "servings_per_package": 1,
                "label_calories_per_serving": 476,
                "macros_per_serving": {
                    "protein_g": 10,
                    "carbs_g": 100,
                    "fat_g": 6,
                    "fiber_g": 0,
                    "sugar_g": 0,
                },
                "confidence": 0.82,
                "label_notes": ["Read from crop image after OCR failed."],
            }
        }
    )
    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        event_publisher=MagicMock(publish=AsyncMock()),
    )

    result = await handler.handle(
        ScanByUrlCommand(
            user_id=_USER_ID,
            image_url=_IMAGE_URL,
            public_id="mealtrack/00000000-0000-0000-0000-000000000006",
            scan_mode="food_label",
            label_crop_image_url=crop_url,
            label_crop_public_id="mealtrack/crop",
            crop_metadata={"crop_strategy": "food_label_visible_frame_v1"},
        )
    )

    assert result.source == "food_label"
    assert result.food_label_metadata["product_name"] == "Danu Ensan Malaysia"
    assert result.nutrition.macros.carbs == pytest.approx(100)
    vision_service.analyze_with_strategy.assert_awaited_once()
    call = vision_service.analyze_with_strategy.await_args
    assert call.args[0] == b"label-crop-image-bytes"
    assert call.args[1].get_strategy_name() == "FoodLabelImageAnalysis"
    assert "OCR" not in call.args[1].get_user_message()
    assert (
        "crop_strategy: food_label_visible_frame_v1" in call.args[1].get_user_message()
    )
    vision_service.analyze.assert_not_called()


@pytest.mark.asyncio
async def test_scan_by_url_food_label_localizes_english_product_name(monkeypatch):
    _install_fake_image_download(
        monkeypatch, responses={_IMAGE_URL: b"full-image-bytes"}
    )
    uow = _make_uow()
    cache = MagicMock()
    cache.enqueue_meal_invalidation = AsyncMock()
    vision_service = MagicMock()
    vision_service.analyze_with_strategy = AsyncMock(
        return_value={
            "structured_data": {
                "is_food_label": True,
                "product_name": "Protein Bar",
                "brand": None,
                "serving_size": {"display_text": "55g", "grams": 55},
                "servings_per_package": 1,
                "label_calories_per_serving": 230,
                "macros_per_serving": {
                    "protein_g": 20,
                    "carbs_g": 20,
                    "fat_g": 8,
                    "fiber_g": 0,
                    "sugar_g": 0,
                },
                "confidence": 0.9,
                "label_notes": [],
            }
        }
    )
    translator = AsyncMock()
    from src.domain.model.translation_result import (
        TranslationOutcome,
        TranslationResult,
    )

    translator.translate_texts.return_value = TranslationResult(
        ("Thanh protein",),
        TranslationOutcome.TRANSLATED,
        "en",
        "vi",
    )
    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        text_translation_service=translator,
        event_publisher=MagicMock(publish=AsyncMock()),
    )

    result = await handler.handle(
        ScanByUrlCommand(
            user_id=_USER_ID,
            image_url=_IMAGE_URL,
            public_id="mealtrack/00000000-0000-0000-0000-000000000099",
            scan_mode="food_label",
            language="vi",
        )
    )

    assert result.source == "food_label"
    assert result.food_label_metadata["product_name"] == "Thanh protein"
    assert result.nutrition.food_items[0].name == "Thanh protein"
    translator.translate_texts.assert_awaited_once_with(["Protein Bar"], "en", "vi")


@pytest.mark.asyncio
async def test_scan_by_url_food_label_uses_full_image_when_crop_missing(monkeypatch):
    _install_fake_image_download(
        monkeypatch, responses={_IMAGE_URL: b"full-image-bytes"}
    )
    uow = _make_uow()
    cache = MagicMock()
    cache.enqueue_meal_invalidation = AsyncMock()
    vision_service = MagicMock()
    vision_service.analyze_with_strategy = AsyncMock(
        return_value={
            "structured_data": {
                "is_food_label": True,
                "product_name": "Protein Bar",
                "brand": None,
                "serving_size": {"display_text": "55g", "grams": 55},
                "servings_per_package": 8,
                "label_calories_per_serving": 210,
                "macros_per_serving": {
                    "protein_g": 12,
                    "carbs_g": 24,
                    "fat_g": 7,
                    "fiber_g": 5,
                    "sugar_g": 8,
                },
                "confidence": 0.9,
                "label_notes": ["Read from full image."],
            }
        }
    )
    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=vision_service,
        gpt_parser=VisionResponseParser(),
        event_publisher=MagicMock(publish=AsyncMock()),
    )

    result = await handler.handle(
        ScanByUrlCommand(
            user_id=_USER_ID,
            image_url=_IMAGE_URL,
            public_id="mealtrack/00000000-0000-0000-0000-000000000007",
            scan_mode="food_label",
        )
    )

    assert result.source == "food_label"
    assert result.food_label_metadata["product_name"] == "Protein Bar"
    call = vision_service.analyze_with_strategy.await_args
    assert call.args[0] == b"full-image-bytes"


@pytest.mark.asyncio
async def test_scan_by_url_food_label_image_ai_failure_does_not_save(monkeypatch):
    _install_fake_image_download(monkeypatch)
    uow = _make_uow()
    vision_service = MagicMock()
    vision_service.analyze_with_strategy = AsyncMock(
        return_value={"structured_data": {"is_food_label": False}}
    )
    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=vision_service,
        gpt_parser=MagicMock(),
    )

    with pytest.raises(ValidationException, match="could not be read"):
        await handler.handle(
            ScanByUrlCommand(
                user_id=_USER_ID,
                image_url=_IMAGE_URL,
                public_id="mealtrack/00000000-0000-0000-0000-000000000004",
                scan_mode="food_label",
            )
        )

    uow.meals.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_by_url_uses_workflow_only_when_graph_enabled():
    expected_meal = object()
    workflow = MagicMock()
    workflow.run_scan_by_url = AsyncMock(return_value=expected_meal)
    handler = ScanByUrlCommandHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=MagicMock(),
        meal_analyze_workflow=workflow,
        meal_analyze_graph_enabled=True,
    )
    command = ScanByUrlCommand(
        user_id=_USER_ID,
        image_url=_IMAGE_URL,
        public_id=_PUBLIC_ID,
    )

    result = await handler.handle(command)

    assert result is expected_meal
    workflow.run_scan_by_url.assert_awaited_once()
    assert workflow.run_scan_by_url.await_args.args[0] == command


@pytest.mark.asyncio
async def test_scan_by_url_graph_disabled_keeps_legacy_path():
    workflow = MagicMock()
    workflow.run_scan_by_url = AsyncMock()
    handler = ScanByUrlCommandHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=MagicMock(),
        meal_analyze_workflow=workflow,
        meal_analyze_graph_enabled=False,
    )
    handler._handle_legacy_scan_by_url = AsyncMock(return_value="legacy-result")
    command = ScanByUrlCommand(
        user_id=_USER_ID,
        image_url=_IMAGE_URL,
        public_id=_PUBLIC_ID,
    )

    result = await handler.handle(command)

    assert result == "legacy-result"
    handler._handle_legacy_scan_by_url.assert_awaited_once_with(command)
    workflow.run_scan_by_url.assert_not_awaited()
