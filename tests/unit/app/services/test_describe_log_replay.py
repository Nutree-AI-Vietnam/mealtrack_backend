"""Idempotency-Key rules for Ghi nhanh: replay, run fresh, or 409 only while running."""

from unittest.mock import AsyncMock

import pytest
from tests.fixtures.fakes.fake_describe_db import FakeDescribeDb

from src.api.exceptions import ConflictException
from src.app.commands.movement import DescribeMovementCommand
from src.app.handlers.command_handlers.describe_movement_command_handler import (
    DescribeMovementCommandHandler,
)
from src.app.services import describe_log_replay as replay
from src.app.services.describe_log_replay import DescribeKey

USER_ID = "user-1"
KEY = DescribeKey(replay.MOVEMENT_DESCRIBE_OPERATION, "key-1", "fingerprint-1")
REPLY = {"entries": [{"id": "mvmt_1"}]}


async def _finished(db: FakeDescribeDb, key: DescribeKey = KEY) -> None:
    reservation, _ = await replay.claim(db.uow, USER_ID, key, lease_seconds=60)
    async with db.uow() as uow:
        await replay.complete(uow, reservation, REPLY)


@pytest.mark.asyncio
async def test_unknown_key_may_run():
    assert await replay.stored_reply(FakeDescribeDb().uow, USER_ID, KEY) is None


@pytest.mark.asyncio
async def test_finished_key_replays_its_reply():
    db = FakeDescribeDb()
    await _finished(db)

    assert await replay.stored_reply(db.uow, USER_ID, KEY) == REPLY


@pytest.mark.asyncio
async def test_other_payload_under_the_same_key_is_a_conflict():
    db = FakeDescribeDb()
    await _finished(db)
    other = DescribeKey(KEY.operation, KEY.idempotency_key, "fingerprint-2")

    with pytest.raises(ConflictException) as exc_info:
        await replay.stored_reply(db.uow, USER_ID, other)

    assert exc_info.value.error_code == "IDEMPOTENCY_KEY_CONFLICT"


@pytest.mark.asyncio
async def test_live_claim_is_in_progress_and_expires_on_its_own():
    db = FakeDescribeDb()
    await replay.claim(db.uow, USER_ID, KEY, lease_seconds=60)

    with pytest.raises(ConflictException) as exc_info:
        await replay.stored_reply(db.uow, USER_ID, KEY)
    db.advance(61)

    assert exc_info.value.error_code == "IDEMPOTENCY_KEY_IN_PROGRESS"
    assert await replay.stored_reply(db.uow, USER_ID, KEY) is None
    reservation, reply = await replay.claim(db.uow, USER_ID, KEY, lease_seconds=60)
    assert reservation.state == "acquired" and reply is None


@pytest.mark.asyncio
async def test_release_never_touches_a_stored_reply():
    db = FakeDescribeDb()
    reservation, _ = await replay.claim(db.uow, USER_ID, KEY, lease_seconds=60)
    async with db.uow() as uow:
        await replay.complete(uow, reservation, REPLY)

    await replay.release(db.uow, reservation)

    assert await replay.stored_reply(db.uow, USER_ID, KEY) == REPLY


@pytest.mark.asyncio
async def test_failed_release_is_logged_not_raised():
    db = FakeDescribeDb()
    reservation, _ = await replay.claim(db.uow, USER_ID, KEY, lease_seconds=60)
    db.fail_release = True

    await replay.release(db.uow, reservation)

    assert db.claim("key-1").status == "in_progress"


def test_lease_outlasts_the_model_deadline():
    assert replay.lease_seconds_for(10.0) == 60
    assert replay.lease_seconds_for(120.0) == 150


@pytest.mark.asyncio
async def test_sentence_finished_between_lookup_and_claim_is_replayed():
    """The handler's own claim replays a reply another request just stored."""
    db = FakeDescribeDb()
    await _finished(db)
    generation = AsyncMock()
    handler = DescribeMovementCommandHandler(
        uow_factory=db.uow, meal_generation_service=generation
    )

    result = await handler.handle(
        DescribeMovementCommand(
            user_id=USER_ID,
            text="chạy 30 phút",
            idempotency_key=KEY.idempotency_key,
            request_fingerprint=KEY.request_fingerprint,
        )
    )

    assert result == REPLY
    generation.generate_meal_plan_async.assert_not_called()


# --- the ids a sentence created, kept for its undo --------------------------

CREATED = {"food": ["meal-1"], "drink": ["drink-1"]}


async def _finished_with_ids(db: FakeDescribeDb, key: DescribeKey = KEY) -> None:
    reservation, _ = await replay.claim(db.uow, USER_ID, key, lease_seconds=60)
    async with db.uow() as uow:
        await replay.complete(uow, reservation, REPLY, CREATED)


@pytest.mark.asyncio
async def test_created_ids_are_stored_but_never_replayed():
    db = FakeDescribeDb()
    await _finished_with_ids(db)

    assert await replay.stored_reply(db.uow, USER_ID, KEY) == REPLY
    assert await replay.claim(db.uow, USER_ID, KEY, lease_seconds=60) == (
        None,
        REPLY,
    )
    async with db.uow() as uow:
        assert (
            await replay.created_entries(
                uow, USER_ID, KEY.operation, KEY.idempotency_key
            )
            == CREATED
        )


@pytest.mark.asyncio
async def test_undo_finds_nothing_for_an_unknown_or_released_sentence():
    db = FakeDescribeDb()
    reservation, _ = await replay.claim(db.uow, USER_ID, KEY, lease_seconds=60)
    await replay.release(db.uow, reservation)

    async with db.uow() as uow:
        released = await replay.created_entries(
            uow, USER_ID, KEY.operation, KEY.idempotency_key
        )
        unknown = await replay.created_entries(uow, USER_ID, KEY.operation, "other")

    assert released is None and unknown is None


@pytest.mark.asyncio
async def test_undo_waits_for_a_sentence_still_being_logged():
    db = FakeDescribeDb()
    await replay.claim(db.uow, USER_ID, KEY, lease_seconds=60)

    async with db.uow() as uow:
        with pytest.raises(ConflictException) as exc_info:
            await replay.created_entries(
                uow, USER_ID, KEY.operation, KEY.idempotency_key
            )

    assert exc_info.value.error_code == "IDEMPOTENCY_KEY_IN_PROGRESS"


@pytest.mark.asyncio
async def test_a_reply_stored_without_ids_undoes_nothing():
    db = FakeDescribeDb()
    await _finished(db)

    async with db.uow() as uow:
        assert (
            await replay.created_entries(
                uow, USER_ID, KEY.operation, KEY.idempotency_key
            )
            == {}
        )
