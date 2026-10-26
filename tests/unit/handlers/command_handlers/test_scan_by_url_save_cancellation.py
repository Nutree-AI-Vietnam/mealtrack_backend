"""A scan cancelled while its meal is saving still publishes the created event.

Background jobs cancel a scan at their deadline or on shutdown. If that lands
between saving the meal and publishing its event, the meal must not end up
saved without the event that updates daily totals.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
_MEAL_ID = "5b0d7f0e-8f7a-4d55-9d0e-6a3c1e2f4b10"


def _make_blocking_uow(commit_started: asyncio.Event, release: asyncio.Event):
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users = MagicMock()
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.meals = MagicMock()
    uow.meals.save = AsyncMock(side_effect=lambda meal: meal)

    async def slow_commit():
        commit_started.set()
        await release.wait()

    uow.commit = AsyncMock(side_effect=slow_commit)
    return uow


def _vision_result() -> dict:
    return {
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


@pytest.mark.asyncio
async def test_meal_saving_when_the_scan_is_cancelled_still_publishes_created():
    commit_started = asyncio.Event()
    release = asyncio.Event()
    published = asyncio.Event()

    async def fake_download(url: str) -> bytes:
        return b"valid-image-bytes"

    async def record_publish(*args, **kwargs):
        published.set()

    handler = ScanByUrlCommandHandler(
        uow=_make_blocking_uow(commit_started, release),
        event_bus=MagicMock(),
        vision_service=MagicMock(),
        gpt_parser=VisionResponseParser(),
        event_publisher=MagicMock(),
        download_image_bytes=fake_download,
    )
    handler.vision_service.analyze = AsyncMock(return_value=_vision_result())

    with patch(
        "src.app.handlers.command_handlers.scan_by_url_command_handler.publish_meal_event",
        new=AsyncMock(side_effect=record_publish),
    ) as publish:
        scan = asyncio.create_task(
            handler.handle(
                ScanByUrlCommand(
                    user_id=_USER_ID,
                    image_url=_CF_IMAGE_URL,
                    public_id=_PUBLIC_ID,
                    meal_id=_MEAL_ID,
                )
            )
        )
        await asyncio.wait_for(commit_started.wait(), timeout=5)
        scan.cancel()
        with pytest.raises(asyncio.CancelledError):
            await scan
        publish.assert_not_called()

        release.set()
        await asyncio.wait_for(published.wait(), timeout=5)

    publish.assert_awaited_once()
    saved_meal = publish.await_args.args[1]
    assert saved_meal.meal_id == _MEAL_ID
    assert publish.await_args.kwargs["event_type"] == "created"
    assert publish.await_args.kwargs["source"] == "scan_by_url"
