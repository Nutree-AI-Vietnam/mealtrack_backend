"""Hoàn tác for the + panel: nothing to undo, and the supplement list put back."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tests.fixtures.fakes.fake_describe_db import FakeDescribeDb

from src.api.exceptions import ResourceNotFoundException
from src.app.commands.describe_any import UndoDescribeAnyCommand
from src.app.handlers.command_handlers.undo_describe_any_command_handler import (
    UndoDescribeAnyCommandHandler,
)
from src.app.services import describe_log_replay as replay

USER_ID = "66666666-6666-6666-6666-666666666666"
DAY = date(2026, 11, 1)


class _Db(FakeDescribeDb):
    def __init__(self, deleted: bool = True):
        super().__init__()
        self.supplements = SimpleNamespace(
            soft_delete_intake=AsyncMock(
                return_value=SimpleNamespace(local_date=DAY) if deleted else None
            ),
            remove_list_entry=AsyncMock(return_value=True),
            update_list_entry=AsyncMock(),
        )

    def uow(self):
        uow = super().uow()
        uow.supplements = self.supplements
        return uow


async def _finished(db: FakeDescribeDb, created: dict) -> None:
    key = replay.DescribeKey(replay.LOG_DESCRIBE_OPERATION, "r-1", "fp")
    reservation, _ = await replay.claim(db.uow, USER_ID, key, lease_seconds=60)
    async with db.uow() as uow:
        await replay.complete(uow, reservation, {"saved": []}, created)


def _handler(db: FakeDescribeDb, purge=None) -> UndoDescribeAnyCommandHandler:
    return UndoDescribeAnyCommandHandler(
        uow_factory=db.uow, event_publisher=AsyncMock(), purge_supplement_day=purge
    )


@pytest.mark.asyncio
async def test_a_sentence_that_saved_nothing_cannot_be_undone():
    with pytest.raises(ResourceNotFoundException) as exc_info:
        await _handler(_Db()).handle(UndoDescribeAnyCommand(USER_ID, "r-1"))

    assert exc_info.value.error_code == "DESCRIBE_REQUEST_NOT_FOUND"


@pytest.mark.asyncio
async def test_undo_puts_the_list_back_as_it_was_and_purges_the_day():
    db, purge = _Db(), AsyncMock()
    doses = [
        {"id": "i-1", "entry_id": "row-1", "listed_before": "absent"},
        {"id": "i-2", "entry_id": "row-2", "listed_before": "inactive"},
        {"id": "i-3", "entry_id": "row-3", "listed_before": "active"},
    ]
    await _finished(db, {"supplement": doses})

    result = await _handler(db, purge).handle(UndoDescribeAnyCommand(USER_ID, "r-1"))

    assert result["deleted"] == {"food": 0, "drink": 0, "activity": 0, "supplement": 3}
    db.supplements.remove_list_entry.assert_awaited_once_with(USER_ID, "row-1")
    db.supplements.update_list_entry.assert_awaited_once_with(
        USER_ID, "row-2", active=False
    )
    purge.assert_awaited_once_with(USER_ID, DAY)


@pytest.mark.asyncio
async def test_a_dose_already_gone_leaves_the_list_alone():
    db = _Db(deleted=False)
    await _finished(
        db,
        {"supplement": [{"id": "i-1", "entry_id": "row-1", "listed_before": "absent"}]},
    )

    result = await _handler(db).handle(UndoDescribeAnyCommand(USER_ID, "r-1"))

    assert result["deleted"]["supplement"] == 0
    db.supplements.remove_list_entry.assert_not_awaited()
