"""``billing_provider_events``: webhook inbox and entitlement sync job queue."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.billing.constants import ProviderEventStatus
from src.domain.model.billing.ledger import BillingProviderEvent
from src.domain.ports.entitlement_ledger_repository_port import (
    BillingProviderEventRepositoryPort,
)
from src.infra.database.models.billing import BillingProviderEventORM as Row

_PARK_REASON_MAX_LENGTH = 32
_DUE_STATUSES = (ProviderEventStatus.RECEIVED, ProviderEventStatus.RETRY)


def _to_domain(row: Any) -> BillingProviderEvent:
    return BillingProviderEvent(
        id=row.id,
        provider=row.provider,
        provider_event_id=row.provider_event_id,
        event_type=row.event_type,
        status=row.status,
        received_at=row.received_at,
        environment=row.environment,
        app_user_id=row.app_user_id,
        event_ts=row.event_ts,
        raw=row.raw,
        user_id=row.user_id,
        park_reason=row.park_reason,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        last_error=row.last_error,
        processed_at=row.processed_at,
    )


_CLEAR_LEASE = {"lease_owner": None, "lease_expires_at": None}


class AsyncBillingProviderEventRepository(BillingProviderEventRepositoryPort):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert_if_absent(self, event: BillingProviderEvent) -> bool:
        statement = (
            pg_insert(Row)
            .values(
                id=event.id,
                provider=event.provider,
                provider_event_id=event.provider_event_id,
                event_type=event.event_type,
                environment=event.environment,
                app_user_id=event.app_user_id,
                event_ts=event.event_ts,
                raw=event.raw,
                user_id=event.user_id,
                status=event.status,
                park_reason=event.park_reason,
                attempts=event.attempts,
                next_attempt_at=event.next_attempt_at,
                lease_owner=event.lease_owner,
                lease_expires_at=event.lease_expires_at,
                last_error=event.last_error,
                received_at=event.received_at,
                processed_at=event.processed_at,
            )
            .on_conflict_do_nothing(index_elements=["provider", "provider_event_id"])
            .returning(Row.id)
        )
        return (await self.session.execute(statement)).first() is not None

    async def get(self, event_id: str) -> BillingProviderEvent | None:
        row = await self.session.get(Row, event_id, populate_existing=True)
        return _to_domain(row) if row else None

    async def get_by_provider_event_id(
        self, provider: str, provider_event_id: str
    ) -> BillingProviderEvent | None:
        row = (
            await self.session.execute(
                select(Row)
                .where(
                    Row.provider == provider,
                    Row.provider_event_id == provider_event_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return _to_domain(row) if row else None

    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> list[BillingProviderEvent]:
        if limit <= 0:
            return []
        # Two queries, each served by its own partial index (ix_bpe_lease_expiry,
        # ix_bpe_due): one OR across both would scan the whole, never-purged table.
        # A crashed worker's expired leases first: they are the oldest work.
        rows = await self._lock(
            select(Row)
            .where(
                Row.status == ProviderEventStatus.PROCESSING,
                Row.lease_expires_at <= now,
            )
            .order_by(Row.lease_expires_at, Row.id),
            limit,
        )
        if len(rows) < limit:
            rows += await self._lock(
                select(Row)
                .where(
                    Row.status.in_(_DUE_STATUSES),
                    or_(Row.next_attempt_at.is_(None), Row.next_attempt_at <= now),
                )
                .order_by(
                    func.coalesce(Row.next_attempt_at, Row.received_at),
                    Row.received_at,
                    Row.id,
                ),
                limit - len(rows),
            )
        for row in rows:
            row.status = ProviderEventStatus.PROCESSING
            row.lease_owner = lease_owner
            row.lease_expires_at = lease_expires_at
        await self.session.flush()
        return [_to_domain(row) for row in rows]

    async def mark_processed(
        self,
        event_id: str,
        *,
        processed_at: datetime,
        user_id: str | None = None,
    ) -> None:
        values: dict = {
            "status": ProviderEventStatus.PROCESSED,
            "processed_at": processed_at,
            "next_attempt_at": None,
            "park_reason": None,
            **_CLEAR_LEASE,
        }
        if user_id is not None:
            values["user_id"] = user_id
        await self._update(event_id, values)

    async def mark_retry(
        self, event_id: str, *, error: str, next_attempt_at: datetime
    ) -> None:
        await self._update(
            event_id,
            {
                "status": ProviderEventStatus.RETRY,
                "attempts": Row.attempts + 1,
                "last_error": error,
                "next_attempt_at": next_attempt_at,
                **_CLEAR_LEASE,
            },
        )

    async def mark_dead(self, event_id: str, *, error: str) -> None:
        await self._update(
            event_id,
            {
                "status": ProviderEventStatus.DEAD,
                "attempts": Row.attempts + 1,
                "last_error": error,
                "next_attempt_at": None,
                **_CLEAR_LEASE,
            },
        )

    async def mark_ignored(self, event_id: str, *, reason: str | None = None) -> None:
        await self._update(
            event_id,
            {
                "status": ProviderEventStatus.IGNORED,
                "park_reason": reason[:_PARK_REASON_MAX_LENGTH] if reason else None,
                "processed_at": func.now(),
                "next_attempt_at": None,
                **_CLEAR_LEASE,
            },
        )

    async def park(self, event_id: str, *, reason: str, drop_raw: bool = False) -> None:
        values: dict = {
            "status": ProviderEventStatus.UNMATCHED,
            "park_reason": reason[:_PARK_REASON_MAX_LENGTH],
            "next_attempt_at": None,
            **_CLEAR_LEASE,
        }
        if drop_raw:
            values["raw"] = None
        await self._update(event_id, values)

    async def purge_raw_for_user(
        self, user_id: str, *, app_user_ids: Sequence[str]
    ) -> int:
        # Rows without a user are only ever parked ones, so both branches are
        # index-backed (ix_bpe_user, ix_bpe_unmatched_app_user).
        owned = Row.user_id == user_id
        if app_user_ids:
            owned = or_(
                owned,
                and_(
                    Row.status == ProviderEventStatus.UNMATCHED,
                    Row.app_user_id.in_(list(app_user_ids)),
                ),
            )
        result = await self.session.execute(
            update(Row)
            .where(owned, Row.raw.is_not(None))
            .values(raw=None)
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def purge_raw_received_before(self, cutoff: datetime, *, limit: int) -> int:
        # Batched through the partial index ix_bpe_raw_retention; rows a worker
        # or webhook holds are skipped and caught by a later batch.
        batch = (
            select(Row.id)
            .where(Row.raw.is_not(None), Row.received_at < cutoff)
            .order_by(Row.received_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self.session.execute(
            update(Row)
            .where(Row.id.in_(batch))
            .values(raw=None)
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def set_user(self, event_id: str, user_id: str) -> None:
        await self._update(event_id, {"user_id": user_id})

    async def requeue(
        self, event_ids: Sequence[str], *, user_id: str, now: datetime
    ) -> int:
        if not event_ids:
            return 0
        result = await self.session.execute(
            update(Row)
            .where(
                Row.id.in_(list(event_ids)),
                Row.status == ProviderEventStatus.UNMATCHED,
            )
            .values(
                status=ProviderEventStatus.RECEIVED,
                user_id=user_id,
                park_reason=None,
                next_attempt_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def list_unmatched(
        self,
        *,
        limit: int,
        app_user_ids: Sequence[str] | None = None,
        after: tuple[datetime, str] | None = None,
    ) -> list[BillingProviderEvent]:
        statement = select(Row).where(Row.status == ProviderEventStatus.UNMATCHED)
        if app_user_ids is not None:
            if not app_user_ids:
                return []
            statement = statement.where(Row.app_user_id.in_(list(app_user_ids)))
        if after is not None:
            received_at, event_id = after
            statement = statement.where(
                or_(
                    Row.received_at > received_at,
                    and_(Row.received_at == received_at, Row.id > event_id),
                )
            )
        rows = await self.session.execute(
            statement.order_by(Row.received_at, Row.id)
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        return [_to_domain(row) for row in rows.scalars()]

    async def list_for_user(
        self, user_id: str, *, limit: int = 20
    ) -> list[BillingProviderEvent]:
        rows = await self.session.execute(
            select(Row)
            .where(Row.user_id == user_id)
            .order_by(Row.received_at.desc(), Row.id.desc())
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        return [_to_domain(row) for row in rows.scalars()]

    async def _lock(self, statement: Any, limit: int) -> list[Any]:
        result = await self.session.execute(
            statement.limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return list(result.scalars().all())

    async def _update(self, event_id: str, values: dict) -> None:
        await self.session.execute(
            update(Row)
            .where(Row.id == event_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
