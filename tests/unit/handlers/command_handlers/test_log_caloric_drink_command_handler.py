from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.app.commands.hydration import LogCaloricDrinkCommand
from src.app.handlers.command_handlers.log_caloric_drink_command_handler import (
    LogCaloricDrinkCommandHandler,
)


class _Users:
    async def find_by_id(self, user_id):
        return None


class _Meals:
    def __init__(self):
        self.saved = None

    async def save(self, meal):
        self.saved = meal
        return meal


class _HydrationEntries:
    def __init__(self):
        self.saved = None

    async def add(self, entry):
        self.saved = entry
        return entry


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
async def test_log_caloric_drink_response_exposes_calories_alias():
    handler = LogCaloricDrinkCommandHandler(uow=_Uow())

    result = await handler.handle(
        LogCaloricDrinkCommand(
            user_id="22222222-2222-2222-2222-222222222222",
            drink_id="coke",
            volume_ml=330,
            target_date=date(2026, 5, 26),
        )
    )

    # coke: kcal_per_100ml=42.1, sugar_per_100ml=10.6 at 330 ml
    # fat_100ml = max(0, (42.1 - 10.6*4)/9) = 0; carbs_100ml = 42.1/4 = 10.525
    # stored: carbs=round(10.525*3.3,1)=34.7, fat=0 → kcal=34.7*4=138.8
    assert result["kcal"] == pytest.approx(138.8)
    assert result["calories"] == pytest.approx(138.8)


@pytest.mark.asyncio
async def test_log_caloric_drink_credits_hydration_weight_and_localizes_name():
    uow = _Uow()
    handler = LogCaloricDrinkCommandHandler(uow=uow)

    result = await handler.handle(
        LogCaloricDrinkCommand(
            user_id="22222222-2222-2222-2222-222222222222",
            drink_id="oj",
            volume_ml=350,
            target_date=date(2026, 5, 26),
            language="vi",
        )
    )

    assert uow.meals.saved is None  # no dual-write; meals table untouched
    assert uow.hydration_entries.saved.legacy_meal_id is None
    assert uow.hydration_entries.saved.credited_ml == 333
    assert result["volume_ml"] == 350  # raw input
    assert result["credited_ml"] == 333  # hydration-weighted amount stored
    assert result["drink_name"] == "Nước ép"
    assert result["meal_id"] == uow.hydration_entries.saved.id  # backward-compat alias


@pytest.mark.asyncio
async def test_log_caloric_drink_publishes_integration_event_post_commit():

    event_publisher = AsyncMock()
    uow = _Uow()
    handler = LogCaloricDrinkCommandHandler(
        uow=uow, event_publisher=event_publisher, environment="staging"
    )

    await handler.handle(
        LogCaloricDrinkCommand(
            user_id="user-1",
            drink_id="coke",
            volume_ml=330,
            target_date=date(2026, 5, 26),
        )
    )

    event_publisher.publish.assert_awaited_once()
    payload = event_publisher.publish.await_args.args[0]
    assert payload["event_type"] == "hydration.caloric_created.v1"
    assert payload["environment"] == "staging"
    assert payload["aggregate_type"] == "hydration"
    assert payload["aggregate_id"] == uow.hydration_entries.saved.id
    assert payload["data"] == {
        "user_id": "user-1",
        "log_date": "2026-05-26",
    }
