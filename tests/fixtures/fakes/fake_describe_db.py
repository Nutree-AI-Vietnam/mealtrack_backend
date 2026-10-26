"""Transactional in-memory store for the Ghi nhanh describe flow.

Each unit of work edits a copy and applies it on a clean exit, like a database
transaction. ``meal_write_operations`` follows the lease rules of
AsyncMealWriteOperationRepository. A write commit can be made to fail before
it applies (nothing saved) or after (saved, but the caller sees an error).
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from src.infra.repositories.meal_write_operation_repository_async import (
    MealWriteReservation,
)


@dataclass
class _Claim:
    id: str
    fingerprint: str
    status: str
    owner: str | None
    generation: int
    expires_at: datetime | None
    response: dict | None = None


@dataclass
class _State:
    claims: dict[tuple[str, str, str], _Claim] = field(default_factory=dict)
    entries: list = field(default_factory=list)
    meals: list = field(default_factory=list)


class FakeDescribeDb:
    def __init__(self) -> None:
        self.state = _State()
        self.now = datetime(2026, 10, 26, 12, tzinfo=UTC)
        # "rolled_back": the entries commit fails and nothing applies.
        # "lost": it applies, then the caller still sees an error.
        self.fail_write_commit: str | None = None
        self.fail_release = False

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def uow(self) -> FakeDescribeUow:
        return FakeDescribeUow(self)

    def claim(self, key: str) -> _Claim | None:
        return next((c for (_, _, k), c in self.state.claims.items() if k == key), None)


class FakeDescribeUow:
    def __init__(self, db: FakeDescribeDb) -> None:
        self.db = db
        self.work: _State = _State()
        self.meal_write_operations = _Claims(self)
        self.hydration_entries = _Rows(self, "entries")
        self.movement_entries = _Rows(self, "entries")
        self.meals = _Rows(self, "meals", method="save")
        self.users = _Users()

    async def __aenter__(self) -> FakeDescribeUow:
        self.work = copy.deepcopy(self.db.state)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            return False
        writes_entries = len(self.work.entries) > len(self.db.state.entries)
        failure = self.db.fail_write_commit if writes_entries else None
        if failure == "rolled_back":
            self.db.fail_write_commit = None
            raise ConnectionError("commit failed")
        self.db.state = self.work
        if failure == "lost":
            self.db.fail_write_commit = None
            raise ConnectionError("connection lost after COMMIT")
        return False


class _Rows:
    def __init__(self, uow: FakeDescribeUow, name: str, method: str = "add") -> None:
        self.uow = uow
        self.name = name
        setattr(self, method, self._append)

    async def _append(self, row):
        getattr(self.uow.work, self.name).append(row)
        return row


class _Users:
    async def get_user_timezone(self, user_id):
        return None

    async def get_profile(self, user_id):
        return None


class _Claims:
    """The lease rules of AsyncMealWriteOperationRepository, in memory."""

    def __init__(self, uow: FakeDescribeUow) -> None:
        self.uow = uow

    @property
    def _now(self) -> datetime:
        return self.uow.db.now

    def _live(self, claim: _Claim) -> bool:
        return (
            claim.status == "in_progress"
            and claim.expires_at is not None
            and claim.expires_at > self._now
        )

    @staticmethod
    def _reservation(claim: _Claim, state: str) -> MealWriteReservation:
        return MealWriteReservation(
            operation_id=claim.id,
            lease_owner=claim.owner,
            lease_generation=claim.generation,
            state=state,
            response=claim.response,
            request_fingerprint=claim.fingerprint,
        )

    async def lookup(self, *, user_id, operation, idempotency_key):
        claim = self.uow.work.claims.get((user_id, operation, idempotency_key))
        if claim is None:
            return None
        state = "replay" if claim.status == "completed" else "available"
        if self._live(claim):
            state = "in_progress"
        return self._reservation(claim, state)

    async def reserve(
        self,
        *,
        user_id,
        operation,
        idempotency_key,
        request_fingerprint,
        lease_seconds=60,
    ):
        claims = self.uow.work.claims
        claim = claims.get((user_id, operation, idempotency_key))
        expires_at = self._now + timedelta(seconds=lease_seconds)
        if claim is None:
            claim = _Claim(
                id=str(uuid.uuid4()),
                fingerprint=request_fingerprint,
                status="in_progress",
                owner=str(uuid.uuid4()),
                generation=1,
                expires_at=expires_at,
            )
            claims[(user_id, operation, idempotency_key)] = claim
            return MealWriteReservation(claim.id, claim.owner, 1, "acquired")
        if claim.fingerprint != request_fingerprint:
            return self._reservation(claim, "fingerprint_conflict")
        if claim.status == "completed":
            return self._reservation(claim, "replay")
        if self._live(claim):
            return self._reservation(claim, "in_progress")
        claim.status = "in_progress"
        claim.owner = str(uuid.uuid4())
        claim.generation += 1
        claim.expires_at = expires_at
        claim.response = None
        return self._reservation(claim, "acquired")

    def _owned(self, reservation) -> _Claim | None:
        claim = next(
            (
                c
                for c in self.uow.work.claims.values()
                if c.id == reservation.operation_id
            ),
            None,
        )
        if (
            claim is not None
            and claim.status == "in_progress"
            and claim.owner == reservation.lease_owner
            and claim.generation == reservation.lease_generation
        ):
            return claim
        return None

    async def complete(self, reservation, *, target_meal_id, response=None):
        claim = self._owned(reservation)
        if claim is None:
            raise RuntimeError("meal write idempotency lease was lost")
        claim.status = "completed"
        claim.response = copy.deepcopy(response)
        claim.owner = None
        claim.expires_at = None

    async def release(self, reservation):
        if self.uow.db.fail_release:
            raise ConnectionError("database unavailable")
        claim = self._owned(reservation)
        if claim is None:
            return
        claim.status = "aborted"
        claim.owner = None
        claim.expires_at = None
