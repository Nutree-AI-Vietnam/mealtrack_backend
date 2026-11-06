"""Weigh-in times sent without an offset are the phone's wall clock."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from src.app.commands.weight import AddWeightEntryCommand, SyncWeightEntriesCommand
from src.app.commands.weight.sync_weight_entries_command import WeightEntryData
from src.app.handlers.command_handlers import (
    add_weight_entry_command_handler as add_module,
)
from src.app.handlers.command_handlers import (
    sync_weight_entries_command_handler as sync_module,
)
from src.domain.utils.timezone_utils import wall_time_to_utc


class _Weights:
    def __init__(self):
        self.saved = []

    async def add(self, entry):
        entry.created_at = datetime.now(UTC)
        self.saved.append(entry)
        return entry

    async def bulk_upsert(self, user_id, entries):
        self.saved.extend(entries)
        return len(entries)


class _Users:
    def __init__(self, timezone_name):
        self.timezone_name = timezone_name

    async def get_user_timezone(self, user_id):
        return self.timezone_name


class _Uow:
    def __init__(self, timezone_name="Asia/Ho_Chi_Minh"):
        self.weight_entries = _Weights()
        self.users = _Users(timezone_name)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        pass


@pytest.fixture
def uow(monkeypatch):
    shared = _Uow()
    monkeypatch.setattr(add_module, "AsyncUnitOfWork", lambda: shared)
    monkeypatch.setattr(sync_module, "AsyncUnitOfWork", lambda: shared)
    return shared


def test_wall_time_is_read_in_the_zone():
    assert wall_time_to_utc(datetime(2026, 10, 26, 20, 18), "Asia/Saigon") == datetime(
        2026, 10, 26, 13, 18, tzinfo=UTC
    )


def test_a_time_with_an_offset_keeps_its_moment():
    sent = datetime(2026, 10, 26, 20, 18, tzinfo=timezone(timedelta(hours=7)))
    assert wall_time_to_utc(sent, "America/New_York") == datetime(
        2026, 10, 26, 13, 18, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_add_reads_a_wall_clock_time_in_the_users_zone(uow):
    await add_module.AddWeightEntryCommandHandler().handle(
        AddWeightEntryCommand(
            user_id="u1", weight_kg=74, recorded_at=datetime(2026, 10, 26, 20, 18)
        )
    )
    assert uow.weight_entries.saved[0].recorded_at == datetime(
        2026, 10, 26, 13, 18, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_add_falls_back_to_the_header_zone(uow):
    uow.users.timezone_name = "UTC"
    await add_module.AddWeightEntryCommandHandler().handle(
        AddWeightEntryCommand(
            user_id="u1",
            weight_kg=74,
            recorded_at=datetime(2026, 10, 26, 8, 0),
            header_timezone="America/New_York",
        )
    )
    assert uow.weight_entries.saved[0].recorded_at == datetime(
        2026, 10, 26, 12, 0, tzinfo=UTC
    )


@pytest.mark.asyncio
async def test_add_keeps_a_utc_time(uow):
    sent = datetime(2026, 10, 26, 13, 18, tzinfo=UTC)
    await add_module.AddWeightEntryCommandHandler().handle(
        AddWeightEntryCommand(user_id="u1", weight_kg=74, recorded_at=sent)
    )
    assert uow.weight_entries.saved[0].recorded_at == sent


@pytest.mark.asyncio
async def test_sync_reads_wall_clock_times_in_the_users_zone(uow):
    utc_sent = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)
    await sync_module.SyncWeightEntriesCommandHandler().handle(
        SyncWeightEntriesCommand(
            user_id="u1",
            entries=[
                WeightEntryData(
                    weight_kg=74, recorded_at=datetime(2026, 10, 26, 20, 18)
                ),
                WeightEntryData(weight_kg=73.8, recorded_at=utc_sent),
            ],
        )
    )
    assert [e.recorded_at for e in uow.weight_entries.saved] == [
        datetime(2026, 10, 26, 13, 18, tzinfo=UTC),
        utc_sent,
    ]
