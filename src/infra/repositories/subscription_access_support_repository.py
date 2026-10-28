"""Read-only SQL behind the subscription access support API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import DateTime, Select, String, func, literal, or_, select, tuple_

from src.domain.ports.subscription_access_support_repository_port import (
    SubscriptionAccessSupportRepositoryPort,
    SupportSubscriptionRecord,
    SupportUserRecord,
    UnresolvedAccessUser,
    UnresolvedQueueCursor,
)
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.database.uow_async import AsyncUnitOfWork

REVENUECAT_PROVIDER = "revenuecat"


def marked_row():
    """A row carrying any provider marker.

    The partial index ix_subscriptions_access_markers has exactly this
    predicate, so every marker query below reads only marked rows (normally
    none) instead of scanning the table.
    """
    return or_(
        Subscription.provider_sync_required_at.is_not(None),
        Subscription.provider_refund_pending_at.is_not(None),
        Subscription.provider_transfer_source_at.is_not(None),
    )


def live_marked_revenuecat_rows(statement: Select) -> Select:
    """Restrict to marked RevenueCat rows of live accounts.

    Deleted and deactivated accounts are left out, as the cutover gate does:
    nothing reads or repairs them (their Firebase uid is a tombstone), so
    their markers would sit in the queue and the alerts forever.
    """
    return statement.join(User, User.id == Subscription.user_id).where(
        Subscription.provider == REVENUECAT_PROVIDER,
        User.is_active.is_(True),
        User.deleted_at.is_(None),
        marked_row(),
    )


def oldest_marker():
    """Each row's oldest marker; PostgreSQL's least() skips NULLs."""
    return func.least(
        Subscription.provider_sync_required_at,
        Subscription.provider_refund_pending_at,
        Subscription.provider_transfer_source_at,
    )


_USER_COLUMNS = (
    User.id,
    User.firebase_uid,
    User.email,
    User.is_active,
    User.deleted_at,
)


def _user_record(row: Any) -> SupportUserRecord:
    return SupportUserRecord(
        id=str(row.id),
        firebase_uid=row.firebase_uid,
        email=row.email,
        is_active=bool(row.is_active),
        deleted_at=row.deleted_at,
    )


def _subscription_record(row: Any) -> SupportSubscriptionRecord:
    """``row`` is a Subscription (typed Any: its legacy Column attributes)."""
    return SupportSubscriptionRecord(
        id=str(row.id),
        provider=str(row.provider),
        status=str(row.status),
        product_id=str(row.product_id),
        platform=str(row.platform),
        purchased_at=row.purchased_at,
        expires_at=row.expires_at,
        grace_period_expires_at=row.grace_period_expires_at,
        period_type=row.period_type,
        provider_snapshot_at=row.provider_snapshot_at,
        provider_sync_required_at=row.provider_sync_required_at,
        provider_refund_pending_at=row.provider_refund_pending_at,
        provider_transfer_source_at=row.provider_transfer_source_at,
        is_sandbox=bool(row.is_sandbox),
    )


class SubscriptionAccessSupportRepository(SubscriptionAccessSupportRepositoryPort):
    """Every call opens a read-only unit of work (rolled back on exit)."""

    def __init__(self, uow_factory: Callable[[], Any] = AsyncUnitOfWork.read_only):
        self._uow_factory = uow_factory

    async def find_users(self, query: str, *, limit: int) -> list[SupportUserRecord]:
        needle = query.strip()
        if not needle:
            return []
        async with self._uow_factory() as uow:
            # id (primary key), firebase_uid and email are each unique and
            # indexed: an index lookup per column.
            exact = await uow.session.execute(
                select(*_USER_COLUMNS)
                .where(
                    or_(
                        User.id == needle,
                        User.firebase_uid == needle,
                        User.email == needle,
                    )
                )
                .order_by(User.created_at, User.id)
                .limit(limit)
            )
            rows = exact.all()
            if not rows and "@" in needle:
                # Support types the address the customer wrote. Only on an
                # exact miss: lower(email) has no index and scans users.
                folded = await uow.session.execute(
                    select(*_USER_COLUMNS)
                    .where(func.lower(User.email) == needle.lower())
                    .order_by(User.created_at, User.id)
                    .limit(limit)
                )
                rows = folded.all()
        return [_user_record(row) for row in rows]

    async def get_user(self, user_id: str) -> SupportUserRecord | None:
        async with self._uow_factory() as uow:
            result = await uow.session.execute(
                select(*_USER_COLUMNS).where(User.id == user_id)
            )
            row = result.first()
        return _user_record(row) if row is not None else None

    async def subscription_rows(self, user_id: str) -> list[SupportSubscriptionRecord]:
        async with self._uow_factory() as uow:
            result = await uow.session.scalars(
                select(Subscription)
                .where(Subscription.user_id == user_id)
                .order_by(
                    Subscription.purchased_at.desc(), Subscription.created_at.desc()
                )
            )
            return [_subscription_record(row) for row in result.all()]

    async def list_unresolved(
        self, *, limit: int, after: UnresolvedQueueCursor | None
    ) -> list[UnresolvedAccessUser]:
        statement = unresolved_users_statement(limit=limit, after=after)
        async with self._uow_factory() as uow:
            rows = (await uow.session.execute(statement)).all()
        return [
            UnresolvedAccessUser(
                user_id=str(row.user_id),
                firebase_uid=row.firebase_uid,
                oldest_marker_at=row.oldest_marker_at,
                sync_required_at=row.sync_required_at,
                refund_pending_at=row.refund_pending_at,
                transfer_source_at=row.transfer_source_at,
            )
            for row in rows
        ]


def unresolved_users_statement(
    *, limit: int, after: UnresolvedQueueCursor | None
) -> Select:
    """One row per live user with marked RevenueCat rows, keyset-paginated
    on (oldest marker, user id)."""
    oldest = func.min(oldest_marker())
    statement = live_marked_revenuecat_rows(
        select(
            Subscription.user_id,
            User.firebase_uid,
            oldest.label("oldest_marker_at"),
            func.min(Subscription.provider_sync_required_at).label("sync_required_at"),
            func.min(Subscription.provider_refund_pending_at).label(
                "refund_pending_at"
            ),
            func.min(Subscription.provider_transfer_source_at).label(
                "transfer_source_at"
            ),
        )
    ).group_by(Subscription.user_id, User.firebase_uid)
    if after is not None:
        statement = statement.having(
            tuple_(oldest, Subscription.user_id)
            > tuple_(
                literal(after.oldest_marker_at, DateTime(timezone=True)),
                literal(after.user_id, String()),
            )
        )
    return statement.order_by(oldest, Subscription.user_id).limit(limit)
