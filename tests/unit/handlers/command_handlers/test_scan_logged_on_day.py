"""A scan counts as logged on its day when its request arrived on that day.

Analysis can end past local midnight. By then a scan for the day before is
stamped like a backfill, so only the instant the request arrived can tell.
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.graphs.meal_analyze import nodes
from src.app.graphs.meal_analyze.runtime import AcquiredImage, MealAnalyzeRuntime
from src.app.handlers.command_handlers.scan_by_url_command_handler import (
    ScanByUrlCommandHandler,
)
from src.app.handlers.command_handlers.upload_meal_image_immediately_command_handler import (
    UploadMealImageImmediatelyHandler,
)
from src.domain.parsers.vision_response_parser import VisionResponseParser

USER_ID = "00000000-0000-0000-0000-000000000001"
IMAGE_ID = "1325c7ca-e012-4df3-b0b4-55bfaeb55eb0"
IMAGE_URL = f"https://imagedelivery.net/PeQb0oPRIbwHNu4iebuEpQ/{IMAGE_ID}/public"
SCAN_DAY = date(2026, 10, 28)
# Times in Saigon: sent 23:59:50 or 00:00:05, analysis done 00:00:10.
BEFORE_MIDNIGHT = datetime(2026, 10, 28, 16, 59, 50, tzinfo=UTC)
AFTER_MIDNIGHT = datetime(2026, 10, 28, 17, 0, 5, tzinfo=UTC)
ANALYSED_AT = datetime(2026, 10, 28, 17, 0, 10, tzinfo=UTC)

ARRIVALS = pytest.mark.parametrize(
    ("received_at", "logged_on_day"),
    [(BEFORE_MIDNIGHT, True), (AFTER_MIDNIGHT, False)],
    ids=["sent-before-midnight", "sent-after-midnight"],
)


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


def _uow():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users.get_user_timezone = AsyncMock(return_value="Asia/Saigon")
    uow.meals.save = AsyncMock(side_effect=lambda meal: meal)
    uow.commit = AsyncMock()
    return uow


def _analysis_ends_after_midnight(module: str):
    """Clocks the save reads: 00:00:10 on the next local day."""
    return (
        patch(f"{module}.utc_now", return_value=ANALYSED_AT),
        patch(f"{module}.user_today", return_value=date(2026, 10, 29)),
        patch(f"{module}.publish_meal_event", new=AsyncMock()),
    )


def _assert_saved(meal, logged_on_day: bool) -> None:
    local = meal.created_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    # Stamped at noon on the scanned day, the way a backfill is.
    assert (local.date(), local.hour, local.minute) == (SCAN_DAY, 12, 0)
    assert meal.logged_on_day is logged_on_day


@pytest.mark.asyncio
@ARRIVALS
async def test_an_uploaded_scan_is_judged_by_when_it_was_sent(
    received_at, logged_on_day
):
    handler = UploadMealImageImmediatelyHandler(
        uow=_uow(), event_bus=MagicMock(), event_publisher=MagicMock()
    )
    handler.image_store = MagicMock(save_async=AsyncMock(return_value=IMAGE_URL))
    handler.vision_service = MagicMock(analyze=AsyncMock(return_value=_vision_result()))
    handler.gpt_parser = VisionResponseParser()
    module = "src.app.handlers.command_handlers.upload_meal_image_immediately_command_handler"
    clock, today, publish = _analysis_ends_after_midnight(module)

    with clock, today, publish:
        meal = await handler.handle(
            UploadMealImageImmediatelyCommand(
                user_id=USER_ID,
                file_contents=b"img",
                content_type="image/jpeg",
                target_date=SCAN_DAY,
                received_at=received_at,
            )
        )

    _assert_saved(meal, logged_on_day)


@pytest.mark.asyncio
@ARRIVALS
async def test_a_scan_by_url_is_judged_by_when_it_was_sent(received_at, logged_on_day):
    async def download(url: str) -> bytes:
        return b"valid-image-bytes"

    handler = ScanByUrlCommandHandler(
        uow=_uow(),
        event_bus=MagicMock(),
        vision_service=MagicMock(analyze=AsyncMock(return_value=_vision_result())),
        gpt_parser=VisionResponseParser(),
        event_publisher=MagicMock(),
        download_image_bytes=download,
    )
    module = "src.app.handlers.command_handlers.scan_by_url_command_handler"
    clock, today, publish = _analysis_ends_after_midnight(module)

    with clock, today, publish:
        meal = await handler.handle(
            ScanByUrlCommand(
                user_id=USER_ID,
                image_url=IMAGE_URL,
                public_id=f"PeQb0oPRIbwHNu4iebuEpQ/{IMAGE_ID}",
                target_date=SCAN_DAY,
                received_at=received_at,
            )
        )

    _assert_saved(meal, logged_on_day)


@pytest.mark.asyncio
@ARRIVALS
async def test_a_scan_saved_by_the_analysis_graph_is_judged_by_when_it_was_sent(
    received_at, logged_on_day
):
    parser = VisionResponseParser()
    runtime = MealAnalyzeRuntime(
        command=ScanByUrlCommand(
            user_id=USER_ID,
            image_url=IMAGE_URL,
            public_id=f"PeQb0oPRIbwHNu4iebuEpQ/{IMAGE_ID}",
            target_date=SCAN_DAY,
            received_at=received_at,
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
        vision_result=_vision_result(),
        nutrition=parser.parse_to_nutrition(_vision_result()),
        gpt_parser=parser,
        uow=_uow(),
        event_publisher=MagicMock(),
        event_bus=MagicMock(),
    )
    clock, today, publish = _analysis_ends_after_midnight(nodes.__name__)

    with clock, today, publish:
        await nodes.persist_meal({}, runtime)

    _assert_saved(runtime.saved_meal, logged_on_day)
