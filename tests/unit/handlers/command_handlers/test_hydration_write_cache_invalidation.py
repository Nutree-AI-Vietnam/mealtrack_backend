"""Hydration writes purge local Redis after commit and before the response.

The app refetches the streak right after a water log, so the purge must finish
inside the request instead of trailing it.
"""

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.hydration import router
from src.app.commands.hydration import (
    DeleteHydrationEntryCommand,
    LogCaloricDrinkCommand,
    LogHydrationCommand,
)
from src.app.commands.meal.delete_meal_command import DeleteMealCommand
from src.app.events.hydration.local_cache_invalidation import (
    register_local_hydration_cache_invalidation_hook,
)
from src.app.events.meal.meal_events import register_local_cache_invalidation_hook
from src.app.handlers.command_handlers.delete_hydration_entry_command_handler import (
    DeleteHydrationEntryCommandHandler,
)
from src.app.handlers.command_handlers.delete_meal_command_handler import (
    DeleteMealCommandHandler,
)
from src.app.handlers.command_handlers.log_caloric_drink_command_handler import (
    LogCaloricDrinkCommandHandler,
)
from src.app.handlers.command_handlers.log_hydration_command_handler import (
    LogHydrationCommandHandler,
)
from src.domain.model.hydration import HydrationEntry
from src.domain.model.meal import MealStatus

USER_ID = "22222222-2222-2222-2222-222222222222"
LOG_DATE = date(2026, 10, 20)


class _Meals:
    def __init__(self, meal=None):
        self.meal = meal

    async def find_by_id(self, meal_id):
        return self.meal

    async def save(self, meal):
        return meal


class _HydrationEntries:
    def __init__(self, entry=None):
        self.entry = entry

    async def add(self, entry):
        return entry

    async def find_by_id_or_legacy_meal_id(self, user_id, entry_id):
        return self.entry

    async def delete_by_id_or_legacy_meal_id(self, user_id, entry_id):
        return True


class _Uow:
    """Records the commit, which happens when the unit of work exits."""

    def __init__(self, events, *, meal=None, entry=None):
        self.events = events
        self.meals = _Meals(meal)
        self.hydration_entries = _HydrationEntries(entry)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.events.append("commit")
        return False


def _publisher(events):
    publisher = AsyncMock()
    publisher.publish.side_effect = lambda payload: events.append("publish")
    return publisher


def _water_entry(**overrides):
    fields = {
        "id": "hydr_water1",
        "user_id": USER_ID,
        "drink_id": "water",
        "drink_name_snapshot": "Water",
        "volume_ml": 250,
        "credited_ml": 250,
        "protein_g": 0.0,
        "carbs_g": 0.0,
        "fat_g": 0.0,
        "fiber_g": 0.0,
        "sugar_g": 0.0,
        "logged_at": datetime(2026, 10, 20, 10, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return HydrationEntry(**fields)


@pytest.fixture
def events():
    return []


@pytest.fixture
def hydration_hook(events):
    async def hook(user_id, log_date):
        # Yield first: a fire-and-forget call would not finish before return.
        await asyncio.sleep(0)
        events.append(("invalidated", user_id, log_date))

    register_local_hydration_cache_invalidation_hook(hook)
    yield
    register_local_hydration_cache_invalidation_hook(None)


@pytest.mark.asyncio
@pytest.mark.usefixtures("hydration_hook")
async def test_water_log_purges_cache_after_commit_before_returning(events):
    handler = LogHydrationCommandHandler(
        _Uow(events), event_publisher=_publisher(events)
    )

    await handler.handle(
        LogHydrationCommand(
            user_id=USER_ID, drink_id="water", volume_ml=250, target_date=LOG_DATE
        )
    )

    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("hydration_hook")
async def test_caloric_drink_log_purges_cache_after_commit_before_returning(events):
    handler = LogCaloricDrinkCommandHandler(
        _Uow(events), event_publisher=_publisher(events)
    )

    await handler.handle(
        LogCaloricDrinkCommand(
            user_id=USER_ID, drink_id="coke", volume_ml=330, target_date=LOG_DATE
        )
    )

    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("hydration_hook")
async def test_hydration_delete_purges_cache_after_commit_before_returning(events):
    handler = DeleteHydrationEntryCommandHandler(
        _Uow(events, entry=_water_entry()), event_publisher=_publisher(events)
    )

    await handler.handle(
        DeleteHydrationEntryCommand(user_id=USER_ID, entry_id="hydr_water1")
    )

    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("hydration_hook")
async def test_legacy_hydration_meal_delete_purges_cache(events):
    legacy_meal = SimpleNamespace(
        meal_id="legacy-meal-1",
        user_id=USER_ID,
        meal_type="hydration",
        status=MealStatus.READY,
        created_at=datetime(2026, 10, 20, 8, 0, tzinfo=UTC),
    )
    legacy_meal.mark_inactive = lambda: legacy_meal
    handler = DeleteHydrationEntryCommandHandler(
        _Uow(events, meal=legacy_meal), event_publisher=_publisher(events)
    )

    await handler.handle(
        DeleteHydrationEntryCommand(user_id=USER_ID, entry_id="legacy-meal-1")
    )

    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("hydration_hook")
async def test_meal_route_hydration_delete_uses_hydration_purge(events):
    async def meal_hook(user_id, meal_date, old_meal_date):
        events.append("meal-invalidated")

    register_local_cache_invalidation_hook(meal_hook)
    try:
        handler = DeleteMealCommandHandler(
            uow=_Uow(events, entry=_water_entry()),
            event_publisher=_publisher(events),
        )
        await handler.handle(DeleteMealCommand(meal_id="hydr_water1", user_id=USER_ID))
    finally:
        register_local_cache_invalidation_hook(None)

    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]


def test_water_log_http_response_waits_for_cache_purge(events):
    handler = LogHydrationCommandHandler(
        _Uow(events), event_publisher=_publisher(events)
    )

    class _Bus:
        async def send(self, command):
            return await handler.handle(command)

    async def fake_user_id():
        return USER_ID

    async def slow_hook(user_id, log_date):
        # Long enough that a purge trailing the response would be missed below.
        await asyncio.sleep(0.05)
        events.append(("invalidated", user_id, log_date))

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = fake_user_id
    app.dependency_overrides[get_configured_event_bus] = lambda: _Bus()

    register_local_hydration_cache_invalidation_hook(slow_hook)
    try:
        response = TestClient(app).post(
            "/v1/hydration/log",
            json={"drink_id": "water", "volume_ml": 250, "target_date": "2026-10-20"},
        )
    finally:
        register_local_hydration_cache_invalidation_hook(None)

    assert response.status_code == 201
    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]
