"""Engagement pushes in the ``notifications`` outbox.

Only rows whose ``notification_type`` is an engagement push kind are read or
written here, so older rows in the table are left alone. Due rows are claimed
with SKIP LOCKED, so any number of dispatchers can share the table. All times
come from ``clock_timestamp()`` so replicas share one clock.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import DateTime, and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.notification import (
    EngagementPushClaim,
    EngagementPushKind,
    EngagementPushStatus,
    NewEngagementPush,
)
from src.domain.ports.engagement_push_repository_port import (
    EngagementPushRepositoryPort,
)
from src.infra.database.models.notification.notification_outbox import (
    NotificationOutboxORM as Outbox,
)

_DEDUPE_CONSTRAINT = "uq_notification_per_user_type_date"
_KINDS = tuple(kind.value for kind in EngagementPushKind)
_PENDING = EngagementPushStatus.PENDING.value
_PROCESSING = EngagementPushStatus.PROCESSING.value
_SENT = EngagementPushStatus.SENT.value
_FAILED = EngagementPushStatus.FAILED.value
_EXPIRED = EngagementPushStatus.EXPIRED.value
_MAX_CLAIM = 100


def _clock():
    return func.clock_timestamp(type_=DateTime(timezone=True))


class AsyncEngagementPushRepository(EngagementPushRepositoryPort):
    """Never commits; callers own the transaction."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def enqueue(self, push: NewEngagementPush) -> bool:
        result = await self.session.execute(
            pg_insert(Outbox)
            .values(
                id=push.id,
                user_id=push.user_id,
                notification_type=push.kind.value,
                scheduled_date=push.scheduled_date,
                scheduled_for_utc=push.scheduled_for_utc,
                status=_PENDING,
                context=dict(push.context),
                expires_at=push.expires_at,
            )
            .on_conflict_do_nothing(constraint=_DEDUPE_CONSTRAINT)
            .returning(Outbox.id)
        )
        return result.scalar_one_or_none() is not None

    async def claim_due(self, *, limit: int) -> list[EngagementPushClaim]:
        if not 1 <= limit <= _MAX_CLAIM:
            raise ValueError("Invalid engagement push batch size")
        now = _clock()
        due = (
            select(Outbox.id)
            .where(
                Outbox.notification_type.in_(_KINDS),
                Outbox.status == _PENDING,
                Outbox.scheduled_for_utc <= now,
                Outbox.expires_at > now,
            )
            .order_by(Outbox.scheduled_for_utc, Outbox.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = (
            await self.session.execute(
                update(Outbox)
                .where(Outbox.id.in_(due.scalar_subquery()))
                .values(status=_PROCESSING)
                .returning(
                    Outbox.id,
                    Outbox.user_id,
                    Outbox.notification_type,
                    Outbox.context,
                    Outbox.scheduled_for_utc,
                )
                .execution_options(synchronize_session=False)
            )
        ).all()
        # RETURNING does not keep the subquery's order.
        rows = sorted(rows, key=lambda row: (row.scheduled_for_utc, row.id))
        return [
            EngagementPushClaim(
                id=row.id,
                user_id=row.user_id,
                kind=EngagementPushKind(row.notification_type),
                context=dict(row.context or {}),
            )
            for row in rows
        ]

    async def mark_sent(self, push_id: str) -> bool:
        return await self._finish(push_id, _SENT)

    async def mark_failed(self, push_id: str) -> bool:
        return await self._finish(push_id, _FAILED)

    async def expire_stale(self) -> int:
        return await self._rowcount(
            update(Outbox)
            .where(
                Outbox.notification_type.in_(_KINDS),
                Outbox.status.in_((_PENDING, _PROCESSING)),
                Outbox.expires_at <= _clock(),
            )
            .values(status=_EXPIRED)
            .execution_options(synchronize_session=False)
        )

    async def delete_pending(self, *, user_id: str, kind: EngagementPushKind) -> int:
        return await self._rowcount(
            delete(Outbox)
            .where(
                Outbox.user_id == user_id,
                Outbox.notification_type == kind.value,
                Outbox.status == _PENDING,
            )
            .execution_options(synchronize_session=False)
        )

    async def _finish(self, push_id: str, status: str) -> bool:
        changed = await self._rowcount(
            update(Outbox)
            .where(and_(Outbox.id == push_id, Outbox.status == _PROCESSING))
            .values(status=status)
            .execution_options(synchronize_session=False)
        )
        return changed > 0

    async def _rowcount(self, statement: Any) -> int:
        # DML results are cursor results, which carry the affected row count.
        result = cast(CursorResult[Any], await self.session.execute(statement))
        return int(result.rowcount or 0)
