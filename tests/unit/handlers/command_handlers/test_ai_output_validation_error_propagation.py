"""Tests that AIOutputValidationError from vision service produces controlled failures."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.domain.exceptions.ai_exceptions import AIOutputValidationError

VALIDATION_ERROR = AIOutputValidationError(
    "Invalid AI output after validation retry",
    purpose="meal_scan",
    attempt_count=2,
    validation_details=["quantity_g: value is not a valid float"],
)


@pytest.mark.asyncio
async def test_upload_handler_propagates_ai_validation_error():
    """AIOutputValidationError from vision_service propagates as-is (not wrapped)."""
    from src.app.commands.meal.upload_meal_image_immediately_command import (
        UploadMealImageImmediatelyCommand,
    )
    from src.app.handlers.command_handlers.upload_meal_image_immediately_command_handler import (
        UploadMealImageImmediatelyHandler,
    )
    from src.domain.services.meal_analysis.fast_path_policy import (
        MealAnalyzeFastPathPolicy,
    )

    handler = UploadMealImageImmediatelyHandler(
        uow=MagicMock(),
        event_bus=MagicMock(),
        fast_path_policy=MealAnalyzeFastPathPolicy(max_attempts=1),
    )
    handler.vision_service = MagicMock()
    handler.vision_service.analyze = AsyncMock(side_effect=VALIDATION_ERROR)

    command = UploadMealImageImmediatelyCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        file_contents=b"img",
        content_type="image/jpeg",
    )

    with pytest.raises(AIOutputValidationError) as exc_info:
        await handler._run_vision_analysis(command, "meal-123")

    assert exc_info.value is VALIDATION_ERROR


@pytest.mark.asyncio
async def test_scan_by_url_handler_propagates_ai_validation_error(monkeypatch):
    """AIOutputValidationError from vision_service.analyze propagates out of handle()."""
    from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
    from src.app.handlers.command_handlers.scan_by_url_command_handler import (
        ScanByUrlCommandHandler,
    )

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
    mock_uow.__aexit__ = AsyncMock(return_value=False)
    mock_uow.users = MagicMock()
    mock_uow.users.get_user_timezone = AsyncMock(return_value="UTC")

    handler = ScanByUrlCommandHandler(
        uow=mock_uow,
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=MagicMock(),
    )
    handler.vision_service.analyze = AsyncMock(side_effect=VALIDATION_ERROR)

    command = ScanByUrlCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        image_url="https://res.cloudinary.com/demo/image/upload/sample.jpg",
        public_id="demo/sample",
    )

    image_bytes = b"fake-image-bytes"
    from src.infra.http import image_download

    shared = MagicMock()
    shared.is_closed = False
    mock_response = MagicMock()
    mock_response.content = image_bytes
    mock_response.raise_for_status = MagicMock()
    shared.get = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(image_download, "_scan_image_client", None)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: shared)

    with patch("asyncio.to_thread", return_value=image_bytes):
        with pytest.raises(AIOutputValidationError) as exc_info:
            await handler.handle(command)

    assert exc_info.value is VALIDATION_ERROR
