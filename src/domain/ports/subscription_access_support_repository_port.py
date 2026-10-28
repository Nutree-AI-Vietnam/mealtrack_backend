"""Read-only port behind the subscription access support API and monitoring.

Support looks users up, reads their access rows, and works through the users
whose RevenueCat rows carry a provider marker (the repair queue); monitoring
counts those markers. Nothing here writes; repairs go through
SubscriptionAccessRefreshPort.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from src.domain.model.subscription_access import (
    UNRESOLVED_REFUND_PENDING,
    UNRESOLVED_SYNC_REQUIRED,
    UNRESOLVED_TRANSFER_SOURCE,
)

# The provider markers a row can carry, as the queue names them.
MARKER_SYNC_REQUIRED = UNRESOLVED_SYNC_REQUIRED
MARKER_REFUND_PENDING = UNRESOLVED_REFUND_PENDING
MARKER_TRANSFER_SOURCE = UNRESOLVED_TRANSFER_SOURCE


@dataclass(frozen=True)
class SupportUserRecord:
    """One account as support sees it, deleted or not."""

    id: str
    firebase_uid: str | None
    email: str | None
    is_active: bool
    deleted_at: datetime | None

    @property
    def is_live(self) -> bool:
        """Not deleted or deactivated (a deleted account's uid is a tombstone)."""
        return self.is_active and self.deleted_at is None


@dataclass(frozen=True)
class SupportSubscriptionRecord:
    """One subscriptions row, any provider (satisfies SubscriptionFacts)."""

    id: str
    provider: str
    status: str
    product_id: str
    platform: str
    purchased_at: datetime
    expires_at: datetime | None
    grace_period_expires_at: datetime | None
    period_type: str | None
    provider_snapshot_at: datetime | None
    provider_sync_required_at: datetime | None
    provider_refund_pending_at: datetime | None
    provider_transfer_source_at: datetime | None
    is_sandbox: bool


@dataclass(frozen=True)
class UnresolvedQueueCursor:
    """Position after one queue entry: oldest marker time, then user id."""

    oldest_marker_at: datetime
    user_id: str


@dataclass(frozen=True)
class UnresolvedAccessUser:
    """A live user with at least one marked RevenueCat row.

    Each ``*_at`` is the oldest marker of that type over the user's rows
    (None when no row carries it); ``oldest_marker_at`` is the oldest of
    them. Marker times are RevenueCat's event times.
    """

    user_id: str
    firebase_uid: str | None
    oldest_marker_at: datetime
    sync_required_at: datetime | None
    refund_pending_at: datetime | None
    transfer_source_at: datetime | None

    @property
    def cursor(self) -> UnresolvedQueueCursor:
        return UnresolvedQueueCursor(self.oldest_marker_at, self.user_id)

    def markers(self) -> list[tuple[str, datetime]]:
        """(marker type, oldest time) for each marker the user's rows carry."""
        found = (
            (MARKER_SYNC_REQUIRED, self.sync_required_at),
            (MARKER_REFUND_PENDING, self.refund_pending_at),
            (MARKER_TRANSFER_SOURCE, self.transfer_source_at),
        )
        return [(kind, at) for kind, at in found if at is not None]


@dataclass(frozen=True)
class AccessMarkerHealth:
    """Provider markers on live users' RevenueCat rows, counted for alerts.

    ``stale_*`` count markers older than the cutoffs passed in (RevenueCat's
    event time before the cutoff). ``oldest_marker_at`` is the oldest marker
    of any type; None when nothing is marked.
    """

    sync_required_users: int
    stale_sync_required_users: int
    refund_pending_rows: int
    stale_refund_pending_rows: int
    transfer_source_rows: int
    oldest_marker_at: datetime | None


class SubscriptionAccessSupportRepositoryPort(ABC):
    """Read-only lookups for support; each call opens its own session."""

    @abstractmethod
    async def find_users(self, query: str, *, limit: int) -> list[SupportUserRecord]:
        """Users whose id, Firebase uid or email equals ``query``.

        An email that matches nobody exactly is retried ignoring case.
        Deleted and deactivated accounts are included.
        """

    @abstractmethod
    async def get_user(self, user_id: str) -> SupportUserRecord | None:
        """The account with this internal id, deleted or not."""

    @abstractmethod
    async def subscription_rows(self, user_id: str) -> list[SupportSubscriptionRecord]:
        """Every subscriptions row of the user, newest purchase first."""

    @abstractmethod
    async def list_unresolved(
        self, *, limit: int, after: UnresolvedQueueCursor | None
    ) -> list[UnresolvedAccessUser]:
        """Live users with a marked RevenueCat row, oldest marker first.

        At most ``limit`` users, strictly after ``after`` in that order.
        """

    @abstractmethod
    async def marker_health(
        self, *, sync_required_before: datetime, refund_pending_before: datetime
    ) -> AccessMarkerHealth:
        """Count the markers on live users' RevenueCat rows in one query."""
