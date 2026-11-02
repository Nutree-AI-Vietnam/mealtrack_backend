"""Idempotency-Key claims for the Ghi nhanh describe commands.

Claims live in meal_write_operation, per user, operation and key, with a
lease. The reply is stored in the same unit of work as the entries, so a key
ends up either with a stored reply (the entries exist) or with nothing saved.
A claim that saved nothing is released at once, or its lease runs out, so a
retry with the same key then runs fresh. 409 IDEMPOTENCY_KEY_IN_PROGRESS only
means a live lease: the first request is still running.

A sentence that can be undone as a whole also stores the ids it created, per
kind, next to its reply. They are never part of a replayed reply.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.api.exceptions import ConflictException

logger = logging.getLogger(__name__)

HYDRATION_DESCRIBE_OPERATION = "hydration_describe"
MOVEMENT_DESCRIBE_OPERATION = "movement_describe"
LOG_DESCRIBE_OPERATION = "log_describe"
# The ids a sentence created, stored with its reply for undo only.
CREATED_KEY = "_created"
# Longer than a describe request can run: the model deadline plus the reads
# and the write around it.
MIN_LEASE_SECONDS = 60


@dataclass(frozen=True)
class DescribeKey:
    operation: str
    idempotency_key: str
    request_fingerprint: str


def lease_seconds_for(ai_timeout_seconds: float) -> int:
    return max(MIN_LEASE_SECONDS, int(ai_timeout_seconds) + 30)


def describe_key(
    operation: str, idempotency_key: str | None, request_fingerprint: str | None
) -> DescribeKey | None:
    if not idempotency_key or not request_fingerprint:
        return None
    return DescribeKey(operation, idempotency_key, request_fingerprint)


async def run_once(
    uow_factory: Any,
    user_id: str,
    key: DescribeKey | None,
    *,
    lease_seconds: int,
    log: Callable[[Any], Awaitable[dict]],
) -> dict:
    """Run ``log(reservation)`` under the key's claim, or replay its stored reply.

    ``log`` must call ``complete`` in the unit of work that saves the entries.
    Any failure releases the claim; the release is fenced, so after a commit
    that raised it leaves the stored reply alone and a retry replays it.
    """
    if key is None:
        return await log(None)
    reservation, reply = await claim(
        uow_factory, user_id, key, lease_seconds=lease_seconds
    )
    if reply is not None:
        return reply
    try:
        return await log(reservation)
    except Exception:
        await release(uow_factory, reservation)
        raise


async def stored_reply(uow_factory: Any, user_id: str, key: DescribeKey) -> dict | None:
    """The stored reply for a finished sentence, else None when it may run."""
    async with uow_factory() as uow:
        found = await uow.meal_write_operations.lookup(
            user_id=user_id,
            operation=key.operation,
            idempotency_key=key.idempotency_key,
        )
    if found is None:
        return None
    _raise_on_conflict(found, key)
    return _public(found.response) if found.state == "replay" else None


async def created_entries(
    uow: Any, user_id: str, operation: str, idempotency_key: str
) -> dict[str, Any] | None:
    """The ids a finished sentence created, or None when it saved nothing.

    The claim row stays locked until the caller's unit of work ends, so a
    second undo waits and then finds everything already gone. 409 while the
    sentence is still being logged, so an undo never races it.
    """
    found = await uow.meal_write_operations.lookup(
        user_id=user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        for_update=True,
    )
    if found is None or found.state == "available":
        return None
    if found.state == "in_progress":
        raise _in_progress()
    return dict((found.response or {}).get(CREATED_KEY) or {})


async def claim(
    uow_factory: Any, user_id: str, key: DescribeKey, *, lease_seconds: int
) -> tuple[Any, dict | None]:
    """Take the lease, or return the reply a request finished in the meantime."""
    async with uow_factory() as uow:
        reservation = await uow.meal_write_operations.reserve(
            user_id=user_id,
            operation=key.operation,
            idempotency_key=key.idempotency_key,
            request_fingerprint=key.request_fingerprint,
            lease_seconds=lease_seconds,
        )
    _raise_on_conflict(reservation, key)
    if reservation.state == "replay":
        return None, _public(reservation.response)
    return reservation, None


async def complete(
    uow: Any,
    reservation: Any,
    response: dict,
    created: dict[str, Any] | None = None,
) -> None:
    """Store the reply inside the unit of work that saves the entries.

    ``created`` (ids per kind) is kept with it for undo, never replayed.
    """
    stored = response if created is None else {**response, CREATED_KEY: created}
    try:
        await uow.meal_write_operations.complete(
            reservation, target_meal_id=None, response=stored
        )
    except RuntimeError as exc:
        # Our lease ran out and another request took the key over; raising
        # here rolls this unit of work back, so nothing is saved twice.
        raise _in_progress() from exc


async def release(uow_factory: Any, reservation: Any) -> None:
    """Free a claim that saved nothing. Fenced: a stored reply is never touched."""
    try:
        async with uow_factory() as uow:
            await uow.meal_write_operations.release(reservation)
    except Exception:
        logger.warning(
            "Describe claim release failed; it frees itself when the lease ends",
            exc_info=True,
        )


def _public(stored: dict | None) -> dict | None:
    if stored is None or CREATED_KEY not in stored:
        return stored
    return {name: value for name, value in stored.items() if name != CREATED_KEY}


def _raise_on_conflict(found: Any, key: DescribeKey) -> None:
    # A freshly inserted claim carries no fingerprint; existing rows do.
    stored_fingerprint = found.request_fingerprint
    if found.state == "fingerprint_conflict" or (
        stored_fingerprint is not None and stored_fingerprint != key.request_fingerprint
    ):
        raise ConflictException(
            "Idempotency-Key reused with a different payload",
            "IDEMPOTENCY_KEY_CONFLICT",
        )
    if found.state == "in_progress":
        raise _in_progress()


def _in_progress() -> ConflictException:
    return ConflictException(
        "This sentence is still being logged", "IDEMPOTENCY_KEY_IN_PROGRESS"
    )
