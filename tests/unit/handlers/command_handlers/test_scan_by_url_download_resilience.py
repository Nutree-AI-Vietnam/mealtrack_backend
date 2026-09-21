"""Unit tests for ScanByUrlCommandHandler image download resilience and fallback."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.handlers.command_handlers.scan_by_url_command_handler import (
    ScanByUrlCommandHandler,
)
from src.domain.parsers.vision_response_parser import VisionResponseParser

_USER_ID = "00000000-0000-0000-0000-000000000001"
_IMAGE_ID = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
_CF_IMAGE_URL = f"https://imagedelivery.net/PeQb0oPRIbwHNu4iebuEpQ/{_IMAGE_ID}/public"
_PUBLIC_ID = f"PeQb0oPRIbwHNu4iebuEpQ/{_IMAGE_ID}"


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


@pytest.mark.asyncio
async def test_scan_by_url_falls_back_to_original_url_when_compressed_url_fails():
    downloaded_urls = []

    async def fake_download(url: str) -> bytes:
        downloaded_urls.append(url)
        if "w=" in url:
            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=httpx.Request("GET", url),
                response=httpx.Response(404),
            )
        return b"valid-original-image-bytes"

    uow = _make_uow()
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=VisionResponseParser(),
        event_publisher=publisher,
        download_image_bytes=fake_download,
        cloudflare_flexible_variants_enabled=True,
    )
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Salad",
                "foods": [
                    {
                        "name": "Salad",
                        "quantity_g": 200,
                        "macros": {
                            "protein_g": 5,
                            "carbs_g": 10,
                            "fat_g": 2,
                            "fiber_g": 3,
                            "sugar_g": 2,
                        },
                    }
                ],
            }
        }
    )

    result = await handler.handle(
        ScanByUrlCommand(
            user_id=_USER_ID,
            image_url=_CF_IMAGE_URL,
            public_id=_PUBLIC_ID,
        )
    )

    assert result is not None
    assert len(downloaded_urls) == 2
    assert "w=768,fit=scale-down,f=auto" in downloaded_urls[0]
    assert downloaded_urls[1] == _CF_IMAGE_URL


@pytest.mark.asyncio
async def test_scan_by_url_uses_original_variant_when_flexible_variants_disabled():
    downloaded_urls = []

    async def fake_download(url: str) -> bytes:
        downloaded_urls.append(url)
        return b"valid-image-bytes"

    uow = _make_uow()
    publisher = MagicMock()
    publisher.publish = AsyncMock()

    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=VisionResponseParser(),
        event_publisher=publisher,
        download_image_bytes=fake_download,
        cloudflare_flexible_variants_enabled=False,
    )
    handler.vision_service.analyze = AsyncMock(
        return_value={
            "structured_data": {
                "is_food": True,
                "dish_name": "Apple",
                "foods": [
                    {
                        "name": "Apple",
                        "quantity_g": 150,
                        "macros": {
                            "protein_g": 0,
                            "carbs_g": 20,
                            "fat_g": 0,
                            "fiber_g": 3,
                            "sugar_g": 15,
                        },
                    }
                ],
            }
        }
    )

    result = await handler.handle(
        ScanByUrlCommand(
            user_id=_USER_ID,
            image_url=_CF_IMAGE_URL,
            public_id=_PUBLIC_ID,
        )
    )

    assert result is not None
    assert downloaded_urls == [_CF_IMAGE_URL]
