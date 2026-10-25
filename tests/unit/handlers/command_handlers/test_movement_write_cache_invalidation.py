"""Movement writes purge local Redis after commit and before the response.

The app refetches the activity list and day totals right after a movement
write, so the purge must finish inside the request instead of trailing it.
"""

import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.routes.v1.movement import router
from src.app.commands.movement import (
    DeleteMovementEntryCommand,
    LogMovementCommand,
    UpdateMovementEntryCommand,
)
from src.app.events.movement.local_cache_invalidation import (
    register_local_movement_cache_invalidation_hook,
)
from src.app.handlers.command_handlers import log_movement_command_handler
from src.app.handlers.command_handlers.delete_movement_entry_command_handler import (
    DeleteMovementEntryCommandHandler,
)
from src.app.handlers.command_handlers.log_movement_command_handler import (
    LogMovementCommandHandler,
)
from src.app.handlers.command_handlers.update_movement_entry_command_handler import (
    UpdateMovementEntryCommandHandler,
)
from src.domain.model.movement import MovementEntry

USER_ID = "33333333-3333-3333-3333-333333333333"
# 18:00 UTC on 19 Oct is 01:00 on 20 Oct in Ho Chi Minh City, so the purge
# must use the user's local day rather than the UTC day.
USER_TZ = "Asia/Ho_Chi_Minh"
LOG_DATE = date(2026, 10, 20)
LATE_EVENING_UTC = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)


class _Users:
    async def get_user_timezone(self, user_id):
        return USER_TZ


class _MovementEntries:
    def __init__(self, entry=None):
        self.entry = entry

    async def add(self, entry):
        return entry

    async def find_by_id(self, user_id, entry_id):
        return self.entry

    async def update(self, user_id, entry_id, **fields):
        for name, value in fields.items():
            setattr(self.entry, name, value)
        return self.entry

    async def delete(self, user_id, entry_id):
        return True


class _Uow:
    """Records the commit, which happens when the unit of work exits."""

    def __init__(self, events, *, entry=None):
        self.events = events
        self.users = _Users()
        self.movement_entries = _MovementEntries(entry)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.events.append("commit")
        return False


def _publisher(events):
    publisher = AsyncMock()
    publisher.publish.side_effect = lambda payload: events.append("publish")
    return publisher


def _manual_entry():
    return MovementEntry(
        id="mvmt_badminton1",
        user_id=USER_ID,
        activity_id="badminton",
        activity_name="Badminton",
        duration_min=45,
        kcal_burned=200.0,
        intensity="moderate",
        logged_at=LATE_EVENING_UTC,
    )


@pytest.fixture
def events():
    return []


@pytest.fixture
def movement_hook(events):
    async def hook(user_id, log_date):
        # Yield first: a fire-and-forget call would not finish before return.
        await asyncio.sleep(0)
        events.append(("invalidated", user_id, log_date))

    register_local_movement_cache_invalidation_hook(hook)
    yield
    register_local_movement_cache_invalidation_hook(None)


@pytest.mark.asyncio
@pytest.mark.usefixtures("movement_hook")
async def test_movement_log_purges_cache_after_commit_before_returning(
    events, monkeypatch
):
    monkeypatch.setattr(
        log_movement_command_handler, "utc_now", lambda: LATE_EVENING_UTC
    )
    handler = LogMovementCommandHandler(
        _Uow(events), event_publisher=_publisher(events)
    )

    await handler.handle(
        LogMovementCommand(
            user_id=USER_ID,
            activity_id="badminton",
            activity_name="Badminton",
            duration_min=45,
            kcal_burned=200.0,
            intensity="moderate",
            include_in_balance=True,
        )
    )

    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("movement_hook")
async def test_movement_update_purges_cache_after_commit_before_returning(events):
    handler = UpdateMovementEntryCommandHandler(
        _Uow(events, entry=_manual_entry()), event_publisher=_publisher(events)
    )

    await handler.handle(
        UpdateMovementEntryCommand(
            user_id=USER_ID,
            entry_id="mvmt_badminton1",
            duration_min=30,
            kcal_burned=150.0,
            intensity="hard",
            include_in_balance=False,
        )
    )

    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("movement_hook")
async def test_movement_delete_purges_cache_after_commit_before_returning(events):
    handler = DeleteMovementEntryCommandHandler(
        _Uow(events, entry=_manual_entry()), event_publisher=_publisher(events)
    )

    await handler.handle(
        DeleteMovementEntryCommand(user_id=USER_ID, entry_id="mvmt_badminton1")
    )

    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]


def test_movement_log_http_response_waits_for_cache_purge(events):
    handler = LogMovementCommandHandler(
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

    register_local_movement_cache_invalidation_hook(slow_hook)
    try:
        response = TestClient(app).post(
            "/v1/movement/log",
            json={
                "activity_id": "badminton",
                "activity_name": "Badminton",
                "duration_min": 45,
                "kcal_burned": 200.0,
                "intensity": "moderate",
                "include_in_balance": True,
                "target_date": "2026-10-20",
            },
        )
    finally:
        register_local_movement_cache_invalidation_hook(None)

    assert response.status_code == 201
    assert events == ["commit", "publish", ("invalidated", USER_ID, LOG_DATE)]
