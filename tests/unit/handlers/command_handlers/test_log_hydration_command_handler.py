from datetime import date
from unittest.mock import AsyncMock

import pytest
from tests.fixtures.fakes.fake_outbox_repository import FakeOutboxRepository

from src.app.commands.hydration.log_hydration_command import LogHydrationCommand
from src.app.handlers.command_handlers.log_hydration_command_handler import (
    LogHydrationCommandHandler,
)


class _Meals:
    async def save(self, meal):
        return meal


class _HydrationEntries:
    async def add(self, entry):
        return entry


class _Uow:
    def __init__(self) -> None:
        self.meals = _Meals()
        self.hydration_entries = _HydrationEntries()
        self.outbox = FakeOutboxRepository()

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

    assert uow.outbox.enqueue_calls == []
    event_publisher.publish.assert_awaited_once()
    event_payload = event_publisher.publish.await_args.args[0]
    assert event_payload["event_type"] == "hydration.created.v1"
    assert event_payload["event_id"]
    assert event_payload["aggregate_id"].startswith("hydr_")
    assert "data" not in event_payload
