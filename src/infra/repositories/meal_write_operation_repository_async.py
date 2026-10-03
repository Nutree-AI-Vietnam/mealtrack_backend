"""Transactional idempotency leases for meal writes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.meal_write_operation import MealWriteOperationORM


@dataclass(frozen=True)
class MealWriteReservation:
    """Reservation outcome returned to an application handler."""

    operation_id: str
    lease_owner: str | None
    lease_generation: int
    state: str
    target_meal_id: str | None = None
    response: dict | None = None
    request_fingerprint: str | None = None


class AsyncMealWriteOperationRepository:
    """Persist and fence user-scoped idempotency leases in the current UoW."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def cleanup_finished(self, *, older_than, limit: int = 100) -> int:
        """Delete only old terminal records in a bounded batch.

        Active leases and recent completed responses remain available for retry
        recovery and idempotent replay.
        """
        result = await self.session.execute(
            select(MealWriteOperationORM.id)
            .where(
                MealWriteOperationORM.status.in_(["completed", "aborted"]),
                MealWriteOperationORM.updated_at < older_than,
            )
            .order_by(MealWriteOperationORM.updated_at)
            .limit(limit)
        )
        operation_ids = list(result.scalars().all())
        if not operation_ids:
            return 0
        await self.session.execute(
            delete(MealWriteOperationORM).where(
                MealWriteOperationORM.id.in_(operation_ids)
            )
        )
        return len(operation_ids)

    async def lookup(self, *, user_id: str, operation: str, idempotency_key: str):
        """Inspect a claim without inserting or extending it during precomputation."""
        result = await self.session.execute(
            select(MealWriteOperationORM).where(
                MealWriteOperationORM.user_id == user_id,
                MealWriteOperationORM.operation == operation,
                MealWriteOperationORM.idempotency_key == idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        state = "replay" if row.status == "completed" else "available"
        if (
            row.status == "in_progress"
            and row.lease_expires_at
            and row.lease_expires_at > utc_now()
        ):
            state = "in_progress"
        return self._reservation(row, state)

    async def reserve(
        self,
        *,
        user_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        lease_seconds: int = 60,
    ) -> MealWriteReservation:
        now = utc_now()
        operation_id = str(uuid.uuid4())
        lease_owner = str(uuid.uuid4())
        insert_stmt = (
            pg_insert(MealWriteOperationORM)
            .values(
                id=operation_id,
                user_id=user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                status="in_progress",
                lease_owner=lease_owner,
                lease_generation=1,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "operation", "idempotency_key"]
            )
            .returning(MealWriteOperationORM.id)
        )
        insert_result = await self.session.execute(insert_stmt)
        inserted_id = insert_result.scalar_one_or_none()
        if inserted_id is not None:
            return MealWriteReservation(
                operation_id=str(inserted_id),
                lease_owner=lease_owner,
                lease_generation=1,
                state="acquired",
            )

        result = await self.session.execute(
            select(MealWriteOperationORM)
            .where(
                MealWriteOperationORM.user_id == user_id,
                MealWriteOperationORM.operation == operation,
                MealWriteOperationORM.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        row = result.scalars().first()
        if row is None:
            raise RuntimeError("meal write idempotency reservation was not visible")

        if row.request_fingerprint != request_fingerprint:
            return self._reservation(row, "fingerprint_conflict")
        return await self._reservation_for_row(row, lease_seconds=lease_seconds)

    async def adopt_fingerprint(
        self,
        reservation: MealWriteReservation,
        *,
        request_fingerprint: str,
    ) -> MealWriteReservation:
        """Keep an identical retry when an older hasher stored a different digest."""

        row = await self._locked_row(reservation.operation_id)
        if row is None:
            raise RuntimeError("meal write idempotency reservation was not visible")
        row.request_fingerprint = request_fingerprint
        await self.session.flush()
        return await self._reservation_for_row(row)

    async def reopen_completed(
        self,
        reservation: MealWriteReservation,
        *,
        lease_seconds: int = 60,
    ) -> MealWriteReservation:
        """Run a completed generate again when its saved plan has no meals."""

        row = await self._locked_row(reservation.operation_id)
        if row is None:
            raise RuntimeError("meal write idempotency reservation was not visible")
        now = utc_now()
        if (
            row.status == "in_progress"
            and row.lease_expires_at
            and row.lease_expires_at > now
        ):
            return self._reservation(row, "in_progress")
        row.status = "in_progress"
        row.lease_owner = str(uuid.uuid4())
        row.lease_generation = int(row.lease_generation or 0) + 1
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.response = None
        await self.session.flush()
        return self._reservation(row, "acquired")

    async def complete(
        self,
        reservation: MealWriteReservation,
        *,
        target_meal_id: str,
        response: dict | None = None,
    ) -> None:
        row = await self._locked_row(reservation.operation_id)
        if not self._owns(row, reservation):
            raise RuntimeError("meal write idempotency lease was lost")
        row.status = "completed"
        row.target_meal_id = target_meal_id
        row.response = response
        row.lease_owner = None
        row.lease_expires_at = None
        await self.session.flush()

    async def release(self, reservation: MealWriteReservation) -> None:
        row = await self._locked_row(reservation.operation_id)
        if not self._owns(row, reservation):
            return
        row.status = "aborted"
        row.lease_owner = None
        row.lease_expires_at = None
        await self.session.flush()

    async def _locked_row(self, operation_id: str) -> MealWriteOperationORM | None:
        result = await self.session.execute(
            select(MealWriteOperationORM)
            .where(MealWriteOperationORM.id == operation_id)
            .with_for_update()
        )
        return result.scalars().first()

    @staticmethod
    def _owns(
        row: MealWriteOperationORM | None, reservation: MealWriteReservation
    ) -> bool:
        return bool(
            row
            and row.status == "in_progress"
            and row.lease_owner == reservation.lease_owner
            and row.lease_generation == reservation.lease_generation
        )

    async def _reservation_for_row(
        self,
        row: MealWriteOperationORM,
        *,
        lease_seconds: int = 60,
    ) -> MealWriteReservation:
        now = utc_now()
        if row.status == "completed":
            return self._reservation(row, "replay")
        if (
            row.status == "in_progress"
            and row.lease_expires_at
            and row.lease_expires_at > now
        ):
            return self._reservation(row, "in_progress")

        row.status = "in_progress"
        row.lease_owner = str(uuid.uuid4())
        row.lease_generation = int(row.lease_generation or 0) + 1
        row.lease_expires_at = now + timedelta(seconds=lease_seconds)
        row.response = None
        row.target_meal_id = None
        await self.session.flush()
        return self._reservation(row, "acquired")

    @staticmethod
    def _reservation(row: MealWriteOperationORM, state: str) -> MealWriteReservation:
        return MealWriteReservation(
            operation_id=row.id,
            lease_owner=row.lease_owner,
            lease_generation=row.lease_generation,
            state=state,
            target_meal_id=row.target_meal_id,
            response=row.response,
            request_fingerprint=row.request_fingerprint,
        )
