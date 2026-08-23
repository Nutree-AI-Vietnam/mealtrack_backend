from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.app.commands.hydration.delete_hydration_entry_command import (
    DeleteHydrationEntryCommand,
)
from src.app.handlers.command_handlers.delete_hydration_entry_command_handler import (
    DeleteHydrationEntryCommandHandler,
)
from src.domain.model.hydration import HydrationEntry


class _Users:
    async def find_by_id(self, user_id):
        return None


class _Meals:
    def __init__(self):
        self.saved = None
        self.deleted_id = None
        self.meal_to_find = None

    async def find_by_id(self, meal_id):
        return self.meal_to_find

    async def save(self, meal):
        self.saved = meal
        return meal


class _HydrationEntries:
    def __init__(self):
        self.entry_to_find = None
        self.deleted = False

    async def find_by_id_or_legacy_meal_id(self, user_id, entry_id):
        return self.entry_to_find

    async def delete_by_id_or_legacy_meal_id(self, user_id, entry_id):
        self.deleted = True
        return True


class _Uow:
    def __init__(self):
        self.users = _Users()
        self.meals = _Meals()
        self.hydration_entries = _HydrationEntries()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_delete_standard_hydration_publishes_hydration_deleted_event():
    uow = _Uow()
    uow.hydration_entries.entry_to_find = HydrationEntry(
        id="hydr_test123",
        user_id="user-1",
        drink_id="water",
        drink_name_snapshot="Water",
        emoji_snapshot="💧",
        volume_ml=250,
        credited_ml=250,
        protein_g=0.0,
        carbs_g=0.0,
        fat_g=0.0,
        fiber_g=0.0,
        sugar_g=0.0,
        logged_at=datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC),
    )
    event_publisher = AsyncMock()
    handler = DeleteHydrationEntryCommandHandler(
        uow=uow, event_publisher=event_publisher, environment="staging"
    )

    result = await handler.handle(
        DeleteHydrationEntryCommand(user_id="user-1", entry_id="hydr_test123")
    )

    assert result == {"success": True}
    assert uow.hydration_entries.deleted is True
    event_publisher.publish.assert_awaited_once()
    payload = event_publisher.publish.await_args.args[0]
    assert payload["event_type"] == "hydration.deleted.v1"
    assert payload["aggregate_type"] == "hydration"
    assert payload["aggregate_id"] == "hydr_test123"
    assert payload["data"] == {
        "user_id": "user-1",
        "log_date": "2026-08-23",
    }


@pytest.mark.asyncio
async def test_delete_caloric_drink_publishes_hydration_caloric_deleted_event():
    uow = _Uow()
    uow.hydration_entries.entry_to_find = HydrationEntry(
        id="hydr_caloric123",
        user_id="user-1",
        drink_id="coke",
        drink_name_snapshot="Coke",
        emoji_snapshot="🥤",
        volume_ml=330,
        credited_ml=330,
        protein_g=0.0,
        carbs_g=35.0,
        fat_g=0.0,
        fiber_g=0.0,
        sugar_g=35.0,
        logged_at=datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC),
    )
    event_publisher = AsyncMock()
    handler = DeleteHydrationEntryCommandHandler(
        uow=uow, event_publisher=event_publisher, environment="staging"
    )

    result = await handler.handle(
        DeleteHydrationEntryCommand(user_id="user-1", entry_id="hydr_caloric123")
    )

    assert result == {"success": True}
    assert uow.hydration_entries.deleted is True
    event_publisher.publish.assert_awaited_once()
    payload = event_publisher.publish.await_args.args[0]
    assert payload["event_type"] == "hydration.caloric_deleted.v1"
    assert payload["aggregate_type"] == "hydration"
    assert payload["aggregate_id"] == "hydr_caloric123"
    assert payload["data"] == {
        "user_id": "user-1",
        "log_date": "2026-08-23",
    }


@pytest.mark.asyncio
async def test_delete_hydration_entry_publisher_exception_does_not_fail_request():

    uow = _Uow()
    uow.hydration_entries.entry_to_find = HydrationEntry(
        id="hydr_test123",
        user_id="user-1",
        drink_id="water",
        drink_name_snapshot="Water",
        emoji_snapshot="💧",
        volume_ml=250,
        credited_ml=250,
        protein_g=0.0,
        carbs_g=0.0,
        fat_g=0.0,
        fiber_g=0.0,
        sugar_g=0.0,
        logged_at=datetime(2026, 8, 23, 10, 0, 0, tzinfo=UTC),
    )
    event_publisher = AsyncMock()
    event_publisher.publish.side_effect = RuntimeError("Queue offline")

    handler = DeleteHydrationEntryCommandHandler(
        uow=uow, event_publisher=event_publisher, environment="staging"
    )

    result = await handler.handle(
        DeleteHydrationEntryCommand(user_id="user-1", entry_id="hydr_test123")
    )

    assert result == {"success": True}
