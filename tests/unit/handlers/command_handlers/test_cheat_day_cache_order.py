from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.app.commands.cheat_day import MarkCheatDayCommand, UnmarkCheatDayCommand
from src.app.handlers.command_handlers.mark_cheat_day_command_handler import (
    MarkCheatDayCommandHandler,
)
from src.app.handlers.command_handlers.unmark_cheat_day_command_handler import (
    UnmarkCheatDayCommandHandler,
)


class TrackingUnitOfWork:
    def __init__(self, events: list[str], existing=None):
        self.events = events
        self.cheat_days = SimpleNamespace(
            find_by_user_and_date=AsyncMock(return_value=existing),
            add=AsyncMock(),
            delete=AsyncMock(),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.events.append("uow_exit")
        return False

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


@pytest.mark.asyncio
async def test_mark_cheat_day_publishes_event_after_commit_and_uow_exit():
    events: list[str] = []
    uow = TrackingUnitOfWork(events)
    publisher = SimpleNamespace(
        publish=AsyncMock(side_effect=lambda *_: events.append("publish"))
    )
    target_date = date(2026, 8, 23)

    with (
        patch(
            "src.app.handlers.command_handlers.mark_cheat_day_command_handler.resolve_user_timezone_async",
            new=AsyncMock(return_value="UTC"),
        ),
        patch(
            "src.app.handlers.command_handlers.mark_cheat_day_command_handler.user_today",
            return_value=date(2026, 8, 22),
        ),
    ):
        await MarkCheatDayCommandHandler(
            uow=uow, event_publisher=publisher, environment="test"
        ).handle(MarkCheatDayCommand(user_id="u1", date=target_date))

    assert events == ["commit", "uow_exit", "publish"]
    publisher.publish.assert_awaited_once()
    payload = publisher.publish.await_args.args[0]
    assert payload["event_type"] == "cheat_day.marked.v1"
    assert payload["data"] == {"user_id": "u1", "date": "2026-08-23"}


@pytest.mark.asyncio
async def test_unmark_cheat_day_publishes_event_after_commit_and_uow_exit():
    events: list[str] = []
    uow = TrackingUnitOfWork(events, existing=SimpleNamespace(cheat_day_id="cheat-1"))
    publisher = SimpleNamespace(
        publish=AsyncMock(side_effect=lambda *_: events.append("publish"))
    )
    target_date = date(2026, 8, 22)

    await UnmarkCheatDayCommandHandler(
        uow=uow, event_publisher=publisher, environment="test"
    ).handle(UnmarkCheatDayCommand(user_id="u1", date=target_date))

    assert events == ["commit", "uow_exit", "publish"]
    publisher.publish.assert_awaited_once()
    payload = publisher.publish.await_args.args[0]
    assert payload["event_type"] == "cheat_day.unmarked.v1"
    assert payload["aggregate_id"] == "cheat-1"
    assert payload["data"] == {"user_id": "u1", "date": "2026-08-22"}

