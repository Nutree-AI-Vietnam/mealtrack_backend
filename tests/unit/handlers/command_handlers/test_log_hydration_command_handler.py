from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest

from src.app.commands.hydration.log_hydration_command import LogHydrationCommand
from src.app.handlers.command_handlers.log_hydration_command_handler import (
    LogHydrationCommandHandler,
)
from src.domain.utils.timezone_utils import ensure_utc, utc_now

USER_ID = "22222222-2222-2222-2222-222222222222"


class _Meals:
    def __init__(self) -> None:
        self.saved = None

    async def save(self, meal):
        self.saved = meal
        return meal


class _HydrationEntries:
    async def add(self, entry):
        return entry


class _Uow:
    def __init__(self) -> None:
        self.meals = _Meals()
        self.hydration_entries = _HydrationEntries()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_hydration_write_publishes_generic_event_after_uow() -> None:
    uow = _Uow()
    event_publisher = AsyncMock()
    await LogHydrationCommandHandler(uow, event_publisher=event_publisher).handle(
        LogHydrationCommand(
            user_id="22222222-2222-2222-2222-222222222222",
            drink_id="water",
            volume_ml=500,
            target_date=date(2026, 8, 23),
        )
    )

    event_publisher.publish.assert_awaited_once()
    event_payload = event_publisher.publish.await_args.args[0]
    assert event_payload["event_type"] == "hydration.created.v1"
    assert event_payload["event_id"]
    assert event_payload["aggregate_id"].startswith("hydr_")
    assert "data" not in event_payload


@pytest.mark.asyncio
async def test_a_drink_logged_for_today_keeps_its_real_time() -> None:
    uow = _Uow()
    before = utc_now()
    await LogHydrationCommandHandler(uow, event_publisher=AsyncMock()).handle(
        LogHydrationCommand(
            user_id=USER_ID,
            drink_id="water",
            volume_ml=250,
            target_date=before.date(),
        )
    )

    assert before <= ensure_utc(uow.meals.saved.created_at) <= utc_now()


@pytest.mark.asyncio
async def test_a_drink_logged_for_a_past_day_is_stamped_at_local_noon() -> None:
    uow = _Uow()
    await LogHydrationCommandHandler(uow, event_publisher=AsyncMock()).handle(
        LogHydrationCommand(
            user_id=USER_ID,
            drink_id="water",
            volume_ml=250,
            target_date=date(2026, 8, 23),
        )
    )

    assert ensure_utc(uow.meals.saved.created_at) == datetime(
        2026, 8, 23, 12, tzinfo=UTC
    )
