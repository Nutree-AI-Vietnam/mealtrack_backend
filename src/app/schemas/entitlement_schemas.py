"""App layer DTOs for ``GET/POST /v1/me/entitlements`` (NM-680 contract, schema v1).

``EntitlementsView.to_contract()`` is the exact JSON body shared with the
Flutter Freezed DTO; routes serialise it as is.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from src.domain.model.billing.constants import (
    ENTITLEMENT_STANDARD,
    EntitlementState,
)
from src.domain.model.billing.ledger import (
    BillingProviderEvent,
    EntitlementGrant,
    StoreSubscription,
    UserEntitlement,
)
from src.domain.services.billing.access_policy import effective_has_access

ENTITLEMENTS_SCHEMA_VERSION = 1
ENTITLEMENTS_TTL_SECONDS = 300
AUTHORITY_BACKEND = "backend"
AUTHORITY_RC = "rc"

_ACCESS_STATES = frozenset(
    {
        EntitlementState.TRIAL,
        EntitlementState.ACTIVE,
        EntitlementState.GRACE,
        EntitlementState.CANCELLED_ACTIVE,
    }
)


def iso_utc(value: datetime | None) -> str | None:
    """ISO-8601 UTC with a ``Z`` suffix, second precision."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_at(projection: UserEntitlement, active: bool) -> str:
    # A cached access state whose access has ended reads as expired.
    if not active and projection.state in _ACCESS_STATES:
        return EntitlementState.EXPIRED
    return projection.state


@dataclass(frozen=True)
class EntitlementDetailView:
    active: bool
    state: str
    access_until: datetime | None
    will_renew: bool | None
    period_type: str | None
    product_id: str | None
    store: str | None
    source: str
    is_sandbox: bool

    @classmethod
    def from_projection(
        cls, projection: UserEntitlement, now: datetime
    ) -> EntitlementDetailView:
        active = effective_has_access(projection, now)
        return cls(
            active=active,
            state=_state_at(projection, active),
            access_until=None if projection.is_lifetime else projection.access_until,
            will_renew=projection.will_renew,
            period_type=projection.period_type,
            product_id=projection.product_id,
            store=projection.store,
            source=projection.primary_source,
            is_sandbox=projection.is_sandbox,
        )

    def to_contract(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "state": self.state,
            "access_until": iso_utc(self.access_until),
            "will_renew": self.will_renew,
            "period_type": self.period_type,
            "product_id": self.product_id,
            "store": self.store,
            "source": self.source,
            "is_sandbox": self.is_sandbox,
        }


@dataclass(frozen=True)
class EntitlementsView:
    user_id: str
    authority: str
    version: int
    server_time: datetime
    has_access: bool
    state: str
    ever_subscribed: bool
    entitlements: dict[str, EntitlementDetailView] = field(default_factory=dict)
    synced_at: datetime | None = None
    stale: bool = False
    sync_error: str | None = None
    ttl_seconds: int = ENTITLEMENTS_TTL_SECONDS
    # Not in the body: drives the ETag and 304 rule.
    next_change_at: datetime | None = None
    # Not in the body: a "no" inside a purchase-hint window is never cached.
    last_purchase_hint_at: datetime | None = None

    @classmethod
    def rc_authority(
        cls,
        user_id: str,
        now: datetime,
        *,
        stale: bool = False,
        sync_error: str | None = None,
    ) -> EntitlementsView:
        """Kill switch / flag off: the app decides from RevenueCat."""
        return cls(
            user_id=user_id,
            authority=AUTHORITY_RC,
            version=0,
            server_time=now,
            has_access=False,
            state=EntitlementState.UNKNOWN,
            ever_subscribed=False,
            stale=stale,
            sync_error=sync_error,
        )

    @classmethod
    def from_projections(
        cls,
        user_id: str,
        projections: Iterable[UserEntitlement],
        now: datetime,
        *,
        stale: bool = False,
        sync_error: str | None = None,
    ) -> EntitlementsView:
        rows = {row.entitlement_key: row for row in projections}
        details = {
            key: EntitlementDetailView.from_projection(row, now)
            for key, row in sorted(rows.items())
        }
        standard = rows.get(ENTITLEMENT_STANDARD)
        if standard is None or (
            standard.last_provider_sync_at is None
            and not details[ENTITLEMENT_STANDARD].active
        ):
            # Never synced with the provider, so a "no" would be a guess:
            # "unknown" tells the app to use its RevenueCat path.
            return cls(
                user_id=user_id,
                authority=AUTHORITY_BACKEND,
                version=0,
                server_time=now,
                has_access=False,
                state=EntitlementState.UNKNOWN,
                ever_subscribed=False,
                entitlements=details,
                stale=stale,
                sync_error=sync_error,
                last_purchase_hint_at=(
                    standard.last_purchase_hint_at if standard is not None else None
                ),
            )
        detail = details[ENTITLEMENT_STANDARD]
        return cls(
            user_id=user_id,
            authority=AUTHORITY_BACKEND,
            version=standard.version,
            server_time=now,
            has_access=detail.active,
            state=detail.state,
            ever_subscribed=standard.ever_had_access,
            entitlements=details,
            synced_at=standard.last_provider_sync_at,
            stale=stale,
            sync_error=sync_error,
            next_change_at=standard.next_change_at,
            last_purchase_hint_at=standard.last_purchase_hint_at,
        )

    @property
    def etag(self) -> str:
        """Weak ETag ``W/"{user_id}:{version}:{next_change_epoch}"``."""
        if self.authority != AUTHORITY_BACKEND:
            return f'W/"{self.user_id}:{self.authority}"'
        epoch = int(self.next_change_at.timestamp()) if self.next_change_at else 0
        return f'W/"{self.user_id}:{self.version}:{epoch}"'

    def is_cacheable_at(self, now: datetime) -> bool:
        """A 304 is allowed only while nothing is scheduled to change."""
        return self.next_change_at is None or now < self.next_change_at

    def is_unconfirmed_negative(self, now: datetime, hint_window: timedelta) -> bool:
        """No access while a purchase hint is recent: never cache this answer.

        The store may not have reported the purchase yet, and the delayed
        re-syncs may still flip it.
        """
        return (
            not self.has_access
            and self.last_purchase_hint_at is not None
            and now - self.last_purchase_hint_at < hint_window
        )

    def to_contract(self) -> dict[str, Any]:
        return {
            "schema_version": ENTITLEMENTS_SCHEMA_VERSION,
            "authority": self.authority,
            "version": self.version,
            "server_time": iso_utc(self.server_time),
            "has_access": self.has_access,
            "state": self.state,
            "ever_subscribed": self.ever_subscribed,
            "entitlements": {
                key: detail.to_contract() for key, detail in self.entitlements.items()
            },
            "synced_at": iso_utc(self.synced_at),
            "stale": self.stale,
            "sync_error": self.sync_error,
            "ttl_seconds": self.ttl_seconds,
        }


@dataclass(frozen=True)
class EntitlementDebugView:
    """Admin debug view: everything the ledger knows about one user."""

    user_id: str
    projections: list[UserEntitlement]
    store_subscriptions: list[StoreSubscription]
    grants: list[EntitlementGrant]
    events: list[BillingProviderEvent]


@dataclass(frozen=True)
class EntitlementGrantResult:
    grant: EntitlementGrant
    projection: UserEntitlement | None
    # False when an idempotent retry returned an existing grant or revoke.
    changed: bool = True
