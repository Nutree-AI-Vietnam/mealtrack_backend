from datetime import UTC, date, datetime

import pytest

from src.api.exceptions import (
    AuthorizationException,
    ResourceNotFoundException,
    ValidationException,
)
from src.app.commands.movement import (
    DeleteMovementEntryCommand,
    LogMovementCommand,
    UpdateMovementEntryCommand,
)
from src.app.handlers.command_handlers import log_movement_command_handler
from src.app.handlers.command_handlers.delete_movement_entry_command_handler import (
    DeleteMovementEntryCommandHandler,
)
from src.app.handlers.command_handlers.log_movement_command_handler import (
    LogMovementCommandHandler,
    _validate_log_movement,
)
from src.app.handlers.command_handlers.update_movement_entry_command_handler import (
    UpdateMovementEntryCommandHandler,
)


def test_validate_log_movement_accepts_catalog_activity():

    _validate_log_movement(
        LogMovementCommand(
            user_id="user-1",
            activity_id="badminton",
            activity_name="Badminton",
            duration_min=60,
            kcal_burned=231.0,
            intensity="moderate",
            include_in_balance=True,
        )
    )


def test_validate_log_movement_rejects_unknown_activity_id():
    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id="unknown",
                activity_name="Unknown",
                duration_min=60,
                kcal_burned=231.0,
                intensity="moderate",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == "INVALID_ACTIVITY"


def test_validate_log_movement_rejects_empty_activity_id():
    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id="",
                activity_name="Custom walk",
                duration_min=60,
                kcal_burned=231.0,
                intensity="moderate",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == "INVALID_ACTIVITY"


@pytest.mark.parametrize("activity_name", ["", "   ", "x" * 101])
def test_validate_log_movement_rejects_invalid_activity_name(activity_name):
    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id=None,
                activity_name=activity_name,
                duration_min=60,
                kcal_burned=231.0,
                intensity="moderate",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == "INVALID_ACTIVITY"


@pytest.mark.parametrize(
    ("duration_min", "error_code"),
    [(0, "INVALID_DURATION"), (-1, "INVALID_DURATION"), (601, "INVALID_DURATION")],
)
def test_validate_log_movement_rejects_invalid_duration(duration_min, error_code):
    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id="badminton",
                activity_name="Badminton",
                duration_min=duration_min,
                kcal_burned=100.0,
                intensity="moderate",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == error_code


def test_validate_log_movement_accepts_zero_kcal():
    _validate_log_movement(
        LogMovementCommand(
            user_id="user-1",
            activity_id=None,
            activity_name="Custom walk",
            duration_min=60,
            kcal_burned=0.0,
            intensity="moderate",
            include_in_balance=True,
        )
    )


def test_validate_log_movement_rejects_negative_kcal():
    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id="badminton",
                activity_name="Badminton",
                duration_min=60,
                kcal_burned=-1.0,
                intensity="moderate",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == "INVALID_KCAL"


def test_validate_log_movement_rejects_invalid_intensity():
    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id="badminton",
                activity_name="Badminton",
                duration_min=60,
                kcal_burned=231.0,
                intensity="extreme",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == "INVALID_INTENSITY"


def test_validate_log_movement_rejects_preset_activity_unsupported_intensity(
    monkeypatch,
):
    monkeypatch.setattr(
        log_movement_command_handler,
        "get_activity",
        lambda activity_id: {"id": activity_id},
    )
    monkeypatch.setattr(
        log_movement_command_handler,
        "get_met",
        lambda activity_id, intensity: None,
    )

    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id="badminton",
                activity_name="Badminton",
                duration_min=60,
                kcal_burned=231.0,
                intensity="moderate",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == "INVALID_INTENSITY"


class _FakeUsers:
    def __init__(self, timezone="UTC"):
        self.timezone = timezone

    async def find_by_id(self, user_id):
        return self


class _FakeMovementEntries:
    def __init__(self):
        self.added = []
        self.deleted = True
        self.entry = None

    async def add(self, entry):
        self.added.append(entry)
        return entry

    async def find_by_id(self, user_id, entry_id):
        return self.entry

    async def delete(self, user_id, entry_id):
        return self.deleted

    async def update(self, user_id, entry_id, **kwargs):
        if self.entry is None:
            return None
        for k, v in kwargs.items():
            setattr(self.entry, k, v)
        return self.entry


class _FakeUow:
    def __init__(self, timezone="UTC"):
        self.users = _FakeUsers(timezone=timezone)
        self.movement_entries = _FakeMovementEntries()
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not exc_type:
            await self.commit()
        return False

    async def commit(self):
        self.committed = True


class _FakeEventPublisher:
    def __init__(self):
        self.published = []

    async def publish(self, payload):
        self.published.append(payload)


@pytest.mark.asyncio
async def test_log_movement_handler_saves_entry_and_publishes_event():
    uow = _FakeUow()
    publisher = _FakeEventPublisher()
    handler = LogMovementCommandHandler(
        uow=uow, event_publisher=publisher, environment="test"
    )

    result = await handler.handle(
        LogMovementCommand(
            user_id="user-1",
            activity_id="badminton",
            activity_name="Badminton",
            duration_min=45,
            kcal_burned=200.5,
            intensity="moderate",
            include_in_balance=True,
            target_date=date(2026, 5, 30),
            header_timezone="Asia/Ho_Chi_Minh",
        )
    )

    saved = uow.movement_entries.added[0]
    assert saved.user_id == "user-1"
    assert saved.activity_id == "badminton"
    assert saved.logged_at == datetime(2026, 5, 30, 5, 0, tzinfo=UTC)
    assert uow.committed is True
    assert result["id"] == saved.id
    assert result["logged_at"] == "2026-05-30T05:00:00+00:00"
    assert len(publisher.published) == 1
    assert publisher.published[0]["event_type"] == "movement.created.v1"
    assert publisher.published[0]["aggregate_type"] == "movement"
    assert publisher.published[0]["aggregate_id"] == saved.id
    assert publisher.published[0]["data"] == {
        "user_id": "user-1",
        "log_date": "2026-05-30",
    }


@pytest.mark.asyncio
async def test_log_movement_without_target_date_uses_current_utc_time(monkeypatch):
    fixed_now = datetime(2026, 5, 30, 23, 30, tzinfo=UTC)
    monkeypatch.setattr(log_movement_command_handler, "utc_now", lambda: fixed_now)
    uow = _FakeUow(timezone="Asia/Ho_Chi_Minh")
    publisher = _FakeEventPublisher()
    handler = LogMovementCommandHandler(
        uow=uow, event_publisher=publisher, environment="test"
    )

    result = await handler.handle(
        LogMovementCommand(
            user_id="user-1",
            activity_id="badminton",
            activity_name="Badminton",
            duration_min=45,
            kcal_burned=200.5,
            intensity="moderate",
            include_in_balance=True,
            target_date=None,
            header_timezone="Asia/Ho_Chi_Minh",
        )
    )

    saved = uow.movement_entries.added[0]
    assert saved.logged_at == fixed_now
    assert result["logged_at"] == "2026-05-30T23:30:00+00:00"
    assert len(publisher.published) == 1
    assert publisher.published[0]["data"] == {
        "user_id": "user-1",
        "log_date": "2026-05-31",
    }


@pytest.mark.asyncio
async def test_delete_movement_handler_raises_not_found_when_entry_missing():
    uow = _FakeUow()
    handler = DeleteMovementEntryCommandHandler(uow=uow)

    with pytest.raises(ResourceNotFoundException) as exc:
        await handler.handle(
            DeleteMovementEntryCommand(user_id="user-1", entry_id="missing")
        )

    assert exc.value.error_code == "ENTRY_NOT_FOUND"
    assert uow.committed is False


@pytest.mark.asyncio
async def test_delete_movement_handler_deletes_commits_and_publishes_event():
    uow = _FakeUow(timezone="Asia/Ho_Chi_Minh")
    publisher = _FakeEventPublisher()
    uow.movement_entries.entry = log_movement_command_handler.MovementEntry(
        id="mvmt_123",
        user_id="user-1",
        activity_id="badminton",
        activity_name="Badminton",
        duration_min=45,
        kcal_burned=200.5,
        intensity="moderate",
        include_in_balance=True,
        logged_at=datetime(2026, 5, 30, 18, 0, tzinfo=UTC),
    )
    handler = DeleteMovementEntryCommandHandler(
        uow=uow, event_publisher=publisher, environment="test"
    )

    result = await handler.handle(
        DeleteMovementEntryCommand(user_id="user-1", entry_id="mvmt_123")
    )

    assert result == {}
    assert uow.movement_entries.deleted is True
    assert uow.committed is True
    assert len(publisher.published) == 1
    assert publisher.published[0]["event_type"] == "movement.deleted.v1"
    assert publisher.published[0]["aggregate_type"] == "movement"
    assert publisher.published[0]["aggregate_id"] == "mvmt_123"
    assert publisher.published[0]["data"] == {
        "user_id": "user-1",
        "log_date": "2026-05-31",
    }


@pytest.mark.asyncio
async def test_update_movement_handler_raises_not_found_when_missing():
    uow = _FakeUow()
    handler = UpdateMovementEntryCommandHandler(uow=uow)

    with pytest.raises(ResourceNotFoundException) as exc:
        await handler.handle(
            UpdateMovementEntryCommand(
                user_id="user-1",
                entry_id="missing",
                duration_min=30,
                kcal_burned=100.0,
                intensity="light",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == "ENTRY_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_movement_handler_rejects_apple_health_entries():
    uow = _FakeUow(timezone="Asia/Ho_Chi_Minh")
    uow.movement_entries.entry = log_movement_command_handler.MovementEntry(
        id="mvmt_apple",
        user_id="user-1",
        activity_name="Running",
        duration_min=60,
        kcal_burned=300.0,
        intensity="hard",
        include_in_balance=True,
        source="apple_health",
        logged_at=datetime(2026, 5, 30, 5, 0, tzinfo=UTC),
    )
    handler = UpdateMovementEntryCommandHandler(uow=uow)

    with pytest.raises(AuthorizationException) as exc:
        await handler.handle(
            UpdateMovementEntryCommand(
                user_id="user-1",
                entry_id="mvmt_apple",
                duration_min=45,
                kcal_burned=200.0,
                intensity="moderate",
                include_in_balance=True,
            )
        )

    assert exc.value.error_code == "APPLE_HEALTH_NOT_EDITABLE"


@pytest.mark.asyncio
async def test_update_movement_handler_updates_and_publishes_event():
    uow = _FakeUow(timezone="Asia/Ho_Chi_Minh")
    publisher = _FakeEventPublisher()
    uow.movement_entries.entry = log_movement_command_handler.MovementEntry(
        id="mvmt_123",
        user_id="user-1",
        activity_name="Badminton",
        duration_min=60,
        kcal_burned=231.0,
        intensity="moderate",
        include_in_balance=True,
        source="manual",
        logged_at=datetime(2026, 5, 30, 18, 0, tzinfo=UTC),
    )
    handler = UpdateMovementEntryCommandHandler(
        uow=uow, event_publisher=publisher, environment="test"
    )

    result = await handler.handle(
        UpdateMovementEntryCommand(
            user_id="user-1",
            entry_id="mvmt_123",
            duration_min=45,
            kcal_burned=173.0,
            intensity="hard",
            include_in_balance=False,
        )
    )

    assert result["duration_min"] == 45
    assert result["kcal_burned"] == 173.0
    assert result["intensity"] == "hard"
    assert result["include_in_balance"] is False
    assert uow.committed is True
    assert len(publisher.published) == 1
    assert publisher.published[0]["event_type"] == "movement.updated.v1"
    assert publisher.published[0]["aggregate_type"] == "movement"
    assert publisher.published[0]["aggregate_id"] == "mvmt_123"
    assert publisher.published[0]["data"] == {
        "user_id": "user-1",
        "log_date": "2026-05-31",
    }



def test_validate_log_movement_rejects_kcal_above_absolute_max():
    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id=None,
                activity_name="Run",
                duration_min=600,
                kcal_burned=5001.0,
                intensity="hard",
                include_in_balance=True,
            )
        )
    assert exc.value.error_code == "INVALID_KCAL"


def test_validate_log_movement_rejects_kcal_unreasonable_for_duration():
    with pytest.raises(ValidationException) as exc:
        _validate_log_movement(
            LogMovementCommand(
                user_id="user-1",
                activity_id=None,
                activity_name="Run",
                duration_min=30,
                kcal_burned=950.0,  # > 30 * 30 = 900
                intensity="hard",
                include_in_balance=True,
            )
        )
    assert exc.value.error_code == "INVALID_KCAL"
