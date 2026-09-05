"""Tests for non-food guard behavior across image command handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.exceptions import ValidationException
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.handlers.command_handlers.scan_by_url_command_handler import (
    ScanByUrlCommandHandler,
)
from src.domain.exceptions.ai_exceptions import MealResponseLocalizationError


def _uow_with_timezone() -> MagicMock:
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users = MagicMock()
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.meals = MagicMock()
    uow.meals.save = AsyncMock()
    uow.meals.find_by_id = AsyncMock()
    uow.commit = AsyncMock()
    return uow


@pytest.mark.asyncio
async def test_scan_by_url_rejects_non_food_before_meal_creation(monkeypatch):
    """scan-by-url should stop before nutrition parsing or persistence."""
    from src.app.handlers.command_handlers import scan_by_url_command_handler as module

    class FakeResponse:
        content = b"fake-image-bytes"

        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(module, "compress_image", lambda raw_bytes: raw_bytes)

    uow = _uow_with_timezone()
    handler = ScanByUrlCommandHandler(uow=uow, event_bus=MagicMock())
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={"structured_data": {"is_food": False, "foods": []}}
    )
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_is_food.return_value = False

    command = ScanByUrlCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        image_url="https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
        public_id="mealtrack/abc",
    )

    with pytest.raises(ValidationException, match="Image does not appear to contain food"):
        await handler.handle(command)

    handler.gpt_parser.parse_to_nutrition.assert_not_called()
    uow.meals.save.assert_not_called()


@pytest.mark.asyncio
async def test_scan_by_url_malformed_localization_container_is_non_retryable(monkeypatch):
    from src.app.handlers.command_handlers import scan_by_url_command_handler as module

    class FakeResponse:
        content = b"fake-image-bytes"

        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(module, "compress_image", lambda raw_bytes: raw_bytes)

    uow = _uow_with_timezone()
    handler = ScanByUrlCommandHandler(uow=uow, event_bus=MagicMock())
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={"structured_data": {"is_food": True, "foods": 1}}
    )
    handler.gpt_parser = MagicMock()

    command = ScanByUrlCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        image_url="https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg",
        public_id="mealtrack/abc",
        language="vi",
    )

    with pytest.raises(MealResponseLocalizationError):
        await handler.handle(command)

    assert handler.vision_service.analyze.call_count == 1
    uow.meals.save.assert_not_called()


@pytest.mark.asyncio
async def test_scan_by_url_captures_rejected_image_for_review(monkeypatch):
    """Expected non-food 400s still create a reviewable Sentry message with image URL."""
    from src.app.handlers.command_handlers import scan_by_url_command_handler as module

    class FakeResponse:
        content = b"fake-image-bytes"

        def raise_for_status(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return FakeResponse()

    capture_message = MagicMock()
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(module, "compress_image", lambda raw_bytes: raw_bytes)
    monkeypatch.setattr(module, "capture_message", capture_message)

    uow = _uow_with_timezone()
    handler = ScanByUrlCommandHandler(uow=uow, event_bus=MagicMock())
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(
        return_value={"structured_data": {"is_food": False, "foods": []}}
    )
    handler.gpt_parser = MagicMock()
    handler.gpt_parser.parse_is_food.return_value = False

    image_url = "https://res.cloudinary.com/test/image/upload/v123/mealtrack/abc.jpg"
    command = ScanByUrlCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        image_url=image_url,
        public_id="mealtrack/abc",
    )

    with pytest.raises(ValidationException, match="Image does not appear to contain food"):
        await handler.handle(command)

    capture_message.assert_called_once_with(
        "meal_scan.image_rejected",
        level="warning",
        context={
            "component": "meal_scan",
            "operation": "scan_by_url",
            "ai_purpose": "meal_scan",
            "image_id": "abc",
            "image_url": image_url,
            "rejection_reason": "parser_not_food",
        },
    )
