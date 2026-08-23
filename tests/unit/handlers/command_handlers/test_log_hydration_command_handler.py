from datetime import date

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
async def test_hydration_write_enqueues_generic_event_in_same_uow() -> None:
    uow = _Uow()
    result = await LogHydrationCommandHandler(uow).handle(
        LogHydrationCommand(
            user_id="22222222-2222-2222-2222-222222222222",
            drink_id="water",
            volume_ml=500,
            target_date=date(2026, 8, 23),
        )
    )

    event_calls = [
        call
        for call in uow.outbox.enqueue_calls
        if call["event_type"] == "hydration.created.v1"
    ]
    legacy_cache_calls = [
        call
        for call in uow.outbox.enqueue_calls
        if call["event_type"] == "cache_invalidation.v1"
    ]
    assert len(event_calls) == 1
    assert legacy_cache_calls == []
    event_call = event_calls[0]
    assert event_call["event_id"] == event_call["payload"]["event_id"]
    assert event_call["aggregate_id"] == event_call["payload"]["data"]["hydration_id"]
    assert event_call["payload"]["data"]["meal_id"] == result["meal_id"]
    assert event_call["payload"]["data"]["volume_ml"] == 500
