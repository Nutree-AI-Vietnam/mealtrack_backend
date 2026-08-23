"""SQLAlchemy async repository for transactional outbox persistence."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.models.outbox_status import OutboxStatus
from src.domain.ports.outbox_repository_port import OutboxRepositoryPort
from src.domain.services.outbox_backoff_service import calculate_next_retry_at
from src.domain.utils.timezone_utils import ensure_utc, utc_now
from src.infra.database.models.outbox_event import TransactionalOutboxORM

if TYPE_CHECKING:
    from src.domain.ports.outbox_handler_port import OutboxHandlerResult

logger = logging.getLogger(__name__)


class AsyncOutboxRepository(OutboxRepositoryPort):
    """Async SQLAlchemy transactional outbox repository. Never calls session.commit()."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def enqueue(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
        max_retries: int = 5,
        scheduled_at: datetime | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
    ) -> TransactionalOutboxORM | None:
        """Insert an outbox event inside a savepoint.

        Returns the TransactionalOutboxORM on success, or None on duplicate event_id (idempotent).
        Using a savepoint (begin_nested) prevents duplicate key violations from aborting
        the caller's outer Unit of Work business transaction.
        """
        now = utc_now()
        effective_scheduled_at = (
            ensure_utc(scheduled_at) if scheduled_at is not None else now
        )

        row = TransactionalOutboxORM(
            id=str(uuid.uuid4()),
            event_id=event_id or str(uuid.uuid4()),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            status=OutboxStatus.PENDING.value,
            retry_count=0,
            max_retries=max_retries,
            next_retry_at=effective_scheduled_at,
            lease_owner=None,
            lease_expires_at=None,
            error_log=[],
            created_at=now,
            updated_at=now,
            processed_at=None,
        )

        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
            return row
        except IntegrityError:
            # Duplicate event_id or unique constraint collision: savepoint was rolled back
            logger.debug(
                "Duplicate outbox event_id '%s' swallowed by savepoint",
                event_id,
            )
            return None

    async def claim_due_records(
        self,
        *,
        worker_id: str,
        batch_size: int = 50,
        lease_duration: timedelta = timedelta(seconds=60),
        now: datetime | None = None,
    ) -> list[TransactionalOutboxORM]:
        """Claim due outbox records using SELECT ... FOR UPDATE SKIP LOCKED.

        Claims both normal PENDING rows due for execution and abandoned IN_PROGRESS
        rows whose leases have expired. Transitions claimed rows to IN_PROGRESS.
        """
        current_time = now or utc_now()
        lease_expires_at = current_time + lease_duration

        stmt = (
            select(TransactionalOutboxORM)
            .where(
                or_(
                    and_(
                        TransactionalOutboxORM.status == OutboxStatus.PENDING.value,
                        TransactionalOutboxORM.next_retry_at <= current_time,
                    ),
                    and_(
                        TransactionalOutboxORM.status == OutboxStatus.IN_PROGRESS.value,
                        TransactionalOutboxORM.lease_expires_at <= current_time,
                    ),
                )
            )
            .order_by(
                TransactionalOutboxORM.next_retry_at.asc(),
                TransactionalOutboxORM.created_at.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )

        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        if rows:
            for row in rows:
                row.status = OutboxStatus.IN_PROGRESS.value
                row.lease_owner = worker_id
                row.lease_expires_at = lease_expires_at
                row.updated_at = current_time
            await self._session.flush()

        return rows

    async def mark_completed(
        self,
        outbox_id: str,
        *,
        processed_at: datetime | None = None,
    ) -> None:
        """Mark an outbox event as COMPLETED and release its lease."""
        now = processed_at or utc_now()
        await self._session.execute(
            update(TransactionalOutboxORM)
            .where(TransactionalOutboxORM.id == outbox_id)
            .values(
                status=OutboxStatus.COMPLETED.value,
                lease_owner=None,
                lease_expires_at=None,
                processed_at=now,
                updated_at=now,
            )
        )
        await self._session.flush()

    async def record_failure(
        self,
        outbox_id: str,
        result: OutboxHandlerResult,
        *,
        max_retries: int | None = None,
    ) -> bool:
        """Record a dispatch failure attempt with structured error log and backoff.

        Returns True if the event was transitioned to FAILED_DEAD_LETTER,
        or False if it was rescheduled as PENDING with exponential backoff.
        """
        stmt = (
            select(TransactionalOutboxORM)
            .where(TransactionalOutboxORM.id == outbox_id)
            .with_for_update()
        )
        db_res = await self._session.execute(stmt)
        row = db_res.scalars().first()
        if row is None:
            return False

        now = utc_now()

        if result.is_paused:
            row.status = OutboxStatus.PENDING.value
            row.lease_owner = None
            row.lease_expires_at = None
            row.next_retry_at = now + timedelta(minutes=5)
            row.updated_at = now
            await self._session.flush()
            return False

        row.retry_count += 1
        effective_max = max_retries if max_retries is not None else row.max_retries

        error_entry = {
            "attempt": row.retry_count,
            "timestamp": now.isoformat(),
            "worker_id": row.lease_owner,
            "error_type": result.error_type or "UnknownError",
            "error_message": result.error_message or "",
            "status_code": result.status_code,
            "is_transient": result.is_transient,
            "metadata": result.metadata or {},
        }

        current_log = list(row.error_log or [])
        current_log.append(error_entry)
        row.error_log = current_log[-10:]

        is_dead_letter = (not result.is_transient) or (row.retry_count >= effective_max)

        if is_dead_letter:
            row.status = OutboxStatus.FAILED_DEAD_LETTER.value
            row.lease_owner = None
            row.lease_expires_at = None
            row.updated_at = now
            await self._session.flush()
            return True
        else:
            row.status = OutboxStatus.PENDING.value
            row.lease_owner = None
            row.lease_expires_at = None
            row.next_retry_at = calculate_next_retry_at(row.retry_count, now=now)
            row.updated_at = now
            await self._session.flush()
            return False

    async def cleanup_outbox_records(
        self,
        *,
        completed_older_than: datetime,
        dead_letter_older_than: datetime,
        batch_size: int = 500,
    ) -> dict[str, int]:
        """Purge old COMPLETED and FAILED_DEAD_LETTER records in bounded batches."""
        # 1. Purge COMPLETED
        completed_stmt = (
            select(TransactionalOutboxORM.id)
            .where(
                TransactionalOutboxORM.status == OutboxStatus.COMPLETED.value,
                TransactionalOutboxORM.updated_at < completed_older_than,
            )
            .order_by(TransactionalOutboxORM.updated_at.asc())
            .limit(batch_size)
        )
        completed_res = await self._session.execute(completed_stmt)
        completed_ids = list(completed_res.scalars().all())
        deleted_completed = 0
        if completed_ids:
            await self._session.execute(
                delete(TransactionalOutboxORM).where(
                    TransactionalOutboxORM.id.in_(completed_ids)
                )
            )
            deleted_completed = len(completed_ids)

        # 2. Purge FAILED_DEAD_LETTER
        dlq_stmt = (
            select(TransactionalOutboxORM.id)
            .where(
                TransactionalOutboxORM.status == OutboxStatus.FAILED_DEAD_LETTER.value,
                TransactionalOutboxORM.updated_at < dead_letter_older_than,
            )
            .order_by(TransactionalOutboxORM.updated_at.asc())
            .limit(batch_size)
        )
        dlq_res = await self._session.execute(dlq_stmt)
        dlq_ids = list(dlq_res.scalars().all())
        deleted_dead_letter = 0
        if dlq_ids:
            await self._session.execute(
                delete(TransactionalOutboxORM).where(
                    TransactionalOutboxORM.id.in_(dlq_ids)
                )
            )
            deleted_dead_letter = len(dlq_ids)

        await self._session.flush()
        return {
            "deleted_completed": deleted_completed,
            "deleted_dead_letter": deleted_dead_letter,
            "total_deleted": deleted_completed + deleted_dead_letter,
        }

    async def find_by_id(self, outbox_id: str) -> TransactionalOutboxORM | None:
        """Find an outbox event by primary key ID."""
        result = await self._session.execute(
            select(TransactionalOutboxORM).where(TransactionalOutboxORM.id == outbox_id)
        )
        return result.scalars().first()

    async def find_by_event_id(self, event_id: str) -> TransactionalOutboxORM | None:
        """Find an outbox event by unique event_id."""
        result = await self._session.execute(
            select(TransactionalOutboxORM).where(
                TransactionalOutboxORM.event_id == event_id
            )
        )
        return result.scalars().first()

    async def get_by_id(self, outbox_id: str) -> TransactionalOutboxORM | None:
        """Retrieve single outbox event by primary ID."""
        return await self.find_by_id(outbox_id)

    async def get_by_event_id(self, event_id: str) -> TransactionalOutboxORM | None:
        """Retrieve single outbox event by unique event_id."""
        return await self.find_by_event_id(event_id)
