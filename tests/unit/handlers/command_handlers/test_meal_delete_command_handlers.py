"""
Unit tests for DeleteMeal (hard delete) command handler.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.commands.meal.delete_meal_command import DeleteMealCommand
from src.app.handlers.command_handlers.delete_meal_command_handler import (
    DeleteMealCommandHandler,
)


@pytest.mark.unit
class TestDeleteMealCommandHandler:
    @pytest.mark.asyncio
    async def test_hard_delete_removes_meal_and_nutrition(
        self, event_bus, meal_repository, sample_meal_db, test_session
    ):
        # Arrange
        meal_id = sample_meal_db.meal_id
        user_id = sample_meal_db.user_id  # Use the same user_id from the sample meal

        # Sanity check original status - meal exists
        meal = meal_repository.find_by_id(meal_id)
        assert meal is not None

        command = DeleteMealCommand(meal_id=meal_id, user_id=user_id)

        # Act - handler now receives UoW via constructor injection (test_uow in event_bus fixture)
        result = await event_bus.send(command)

        # Assert response
        assert result["meal_id"] == meal_id
        assert "message" in result

        # Assert persisted state - meal should be hard deleted
        updated = meal_repository.find_by_id(meal_id)
        assert updated is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_meal_is_idempotent(self, event_bus, test_session):
        meal_id = "00000000-0000-0000-0000-000000000000"
        user_id = "123e4567-e89b-12d3-a456-426614174000"
        command = DeleteMealCommand(meal_id=meal_id, user_id=user_id)

        result = await event_bus.send(command)

        assert result["meal_id"] == meal_id
        assert result["message"] == "Meal already deleted"


@pytest.mark.asyncio
async def test_delete_meal_command_deletes_hydration_entry_alias():
    entry_id = "11111111-1111-1111-1111-111111111111"
    user_id = "22222222-2222-2222-2222-222222222222"
    logged_at = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)

    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.outbox = None
    uow.meals.find_by_id = AsyncMock(return_value=None)
    uow.hydration_entries.find_by_id_or_legacy_meal_id = AsyncMock(
        return_value=SimpleNamespace(id=entry_id, logged_at=logged_at)
    )
    uow.hydration_entries.delete_by_id_or_legacy_meal_id = AsyncMock(return_value=True)

    event_publisher = AsyncMock()

    handler = DeleteMealCommandHandler(
        uow=uow, event_publisher=event_publisher, environment="staging"
    )

    result = await handler.handle(DeleteMealCommand(meal_id=entry_id, user_id=user_id))

    assert result == {"meal_id": entry_id, "message": "Hydration entry deleted"}
    uow.hydration_entries.delete_by_id_or_legacy_meal_id.assert_awaited_once_with(
        user_id, entry_id
    )
    event_publisher.publish.assert_awaited_once()
    payload = event_publisher.publish.await_args.args[0]
    assert payload["event_type"] == "hydration.deleted.v1"
    assert payload["aggregate_id"] == entry_id
    assert payload["data"] == {
        "user_id": user_id,
        "log_date": "2026-06-16",
    }

