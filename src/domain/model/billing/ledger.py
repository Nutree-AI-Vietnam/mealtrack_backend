"""Entitlement ledger records, mirroring the NM-680 tables.

``StoreSubscription`` and ``UserEntitlement`` are written only by the entitlement
sync service; ``EntitlementGrant`` only by the admin grant handlers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from src.domain.exceptions.billing_exceptions import InvalidEntitlementGrant
from src.domain.model.billing.constants import (
    DEFAULT_PROJECT,
    GRANT_MAX_DAYS,
    GrantSource,
    ProviderEventStatus,
    StoreSubscriptionState,
)
from src.domain.model.billing.snapshot import NormalizedSubscription

GRANT_REASON_MAX_LENGTH = 255
# Latest end a grant may have, measured from when it is issued; the minutes
# absorb an admin client whose clock runs slightly ahead.
GRANT_MAX_HORIZON = timedelta(days=GRANT_MAX_DAYS, minutes=5)


@dataclass(frozen=True)
class BillingCustomerIdentity:
    """One provider customer id that belongs to one user.

    Only the ``primary`` identity is fetched for access; aliases route re-syncs
    and never grant access on their own.
    """

    id: str
    user_id: str
    provider: str
    provider_customer_id: str
    kind: str
    first_seen_at: datetime
    last_seen_at: datetime
    project: str = DEFAULT_PROJECT
    retired_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_retired(self) -> bool:
        return self.retired_at is not None


@dataclass(frozen=True)
class BillingProviderEvent:
    """Inbox row: one provider webhook, doubling as a sync job."""

    id: str
    provider: str
    provider_event_id: str
    event_type: str
    status: str
    received_at: datetime
    environment: str | None = None
    app_user_id: str | None = None
    event_ts: datetime | None = None
    raw: dict[str, Any] | None = None
    user_id: str | None = None
    park_reason: str | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    last_error: str | None = None
    processed_at: datetime | None = None

    @property
    def is_settled(self) -> bool:
        """A duplicate delivery of a settled row is acknowledged without work."""
        return self.status in (
            ProviderEventStatus.PROCESSED,
            ProviderEventStatus.IGNORED,
        )


@dataclass(frozen=True)
class StoreSubscription:
    """A store subscription row as last reported by its customer's snapshot."""

    id: str
    user_id: str
    provider: str
    store: str
    environment: str
    subscription_key: str
    source_customer_id: str
    product_id: str
    state: str
    fetched_at: datetime
    last_synced_at: datetime
    entitlement_key: str | None = None
    store_identifier: str | None = None
    original_transaction_id: str | None = None
    ownership_type: str | None = None
    period_type: str | None = None
    will_renew: bool | None = None
    is_lifetime: bool = False
    purchased_at: datetime | None = None
    expires_at: datetime | None = None
    grace_expires_at: datetime | None = None
    unsubscribe_detected_at: datetime | None = None
    billing_issue_detected_at: datetime | None = None
    refunded_at: datetime | None = None
    absent_since: datetime | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_snapshot(
        cls,
        sub: NormalizedSubscription,
        *,
        row_id: str,
        user_id: str,
        provider: str,
        fetched_at: datetime,
        now: datetime,
    ) -> StoreSubscription:
        """Build a new row for a subscription first seen in this snapshot."""
        return cls(
            id=row_id,
            user_id=user_id,
            provider=provider,
            fetched_at=fetched_at,
            last_synced_at=now,
            version=1,
            created_at=now,
            updated_at=now,
            **_snapshot_fields(sub),
        )

    def accepts_snapshot(self, fetched_at: datetime) -> bool:
        """Older snapshots never overwrite newer ones."""
        return fetched_at >= self.fetched_at

    def refreshed_from(
        self,
        sub: NormalizedSubscription,
        *,
        user_id: str,
        fetched_at: datetime,
        now: datetime,
    ) -> StoreSubscription:
        """Apply a newer snapshot; ``user_id`` may move the row to another user."""
        return replace(
            self,
            user_id=user_id,
            fetched_at=fetched_at,
            last_synced_at=now,
            absent_since=None,
            version=self.version + 1,
            updated_at=now,
            **_snapshot_fields(sub),
        )

    def marked_absent(
        self, *, fetched_at: datetime, now: datetime
    ) -> StoreSubscription:
        """The customer's latest snapshot no longer lists this subscription."""
        return replace(
            self,
            state=StoreSubscriptionState.ABSENT,
            absent_since=self.absent_since or now,
            fetched_at=fetched_at,
            last_synced_at=now,
            version=self.version + 1,
            updated_at=now,
        )


def _snapshot_fields(sub: NormalizedSubscription) -> dict[str, Any]:
    return {
        "store": sub.store,
        "environment": sub.environment,
        "subscription_key": sub.subscription_key,
        "source_customer_id": sub.source_customer_id,
        "product_id": sub.product_id,
        "state": sub.state,
        "entitlement_key": sub.entitlement_key,
        "store_identifier": sub.store_identifier,
        "original_transaction_id": sub.original_transaction_id,
        "ownership_type": sub.ownership_type,
        "period_type": sub.period_type,
        "will_renew": sub.will_renew,
        "is_lifetime": sub.is_lifetime,
        "purchased_at": sub.purchased_at,
        "expires_at": sub.expires_at,
        "grace_expires_at": sub.grace_expires_at,
        "unsubscribe_detected_at": sub.unsubscribe_detected_at,
        "billing_issue_detected_at": sub.billing_issue_detected_at,
        "refunded_at": sub.refunded_at,
    }


@dataclass(frozen=True)
class EntitlementGrant:
    """A manual grant from our own ledger (D5). Append-only, revoke-only."""

    id: str
    user_id: str
    entitlement_key: str
    source: str
    reason: str
    starts_at: datetime
    ends_at: datetime
    created_by: str
    source_ref: str | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None
    revoked_by: str | None = None
    rc_mirror_ref: str | None = None
    created_at: datetime | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_live_at(self, now: datetime) -> bool:
        return not self.is_revoked and self.starts_at <= now < self.ends_at

    def revoked(self, *, at: datetime, reason: str, by: str) -> EntitlementGrant:
        return replace(self, revoked_at=at, revoke_reason=reason, revoked_by=by)


@dataclass(frozen=True)
class UserEntitlement:
    """Projection row: the answer to "does this user have access to this key?".

    ``has_access`` is a cache as of ``computed_at``; readers go through
    ``access_policy.effective_has_access`` and recompute once ``next_change_at``
    has passed.
    """

    user_id: str
    entitlement_key: str
    has_access: bool
    state: str
    primary_source: str
    version: int
    computed_at: datetime
    access_until: datetime | None = None
    is_lifetime: bool = False
    next_change_at: datetime | None = None
    will_renew: bool | None = None
    period_type: str | None = None
    primary_ref: str | None = None
    product_id: str | None = None
    store: str | None = None
    is_sandbox: bool = False
    ever_had_access: bool = False
    last_provider_sync_at: datetime | None = None
    last_purchase_hint_at: datetime | None = None


def validate_grant_request(
    *,
    source: str,
    reason: str,
    starts_at: datetime | None,
    ends_at: datetime,
    now: datetime | None = None,
) -> None:
    """Enforce D5 before a grant reaches the ledger (the DB CHECK is a backstop).

    ``starts_at=None`` (it defaults to the issue time) and ``now=None`` skip the
    checks that need them; the grant handler runs the full check once both are
    known from its clock.
    """
    if source not in set(GrantSource):
        allowed = ", ".join(sorted(GrantSource))
        raise InvalidEntitlementGrant(f"source must be one of: {allowed}")
    if not reason or not reason.strip():
        raise InvalidEntitlementGrant("reason is required")
    if len(reason) > GRANT_REASON_MAX_LENGTH:
        raise InvalidEntitlementGrant(
            f"reason must be at most {GRANT_REASON_MAX_LENGTH} characters"
        )
    if ends_at.tzinfo is None or (starts_at is not None and starts_at.tzinfo is None):
        raise InvalidEntitlementGrant("starts_at and ends_at must be timezone-aware")
    if starts_at is not None:
        if ends_at <= starts_at:
            raise InvalidEntitlementGrant("ends_at must be after starts_at")
        if ends_at > starts_at + timedelta(days=GRANT_MAX_DAYS):
            raise InvalidEntitlementGrant(
                f"a grant may last at most {GRANT_MAX_DAYS} days"
            )
    # The cap bounds total grant access, not each grant: back-to-back grants
    # cannot stack into multi-year access without a later review.
    if now is not None and ends_at > now + GRANT_MAX_HORIZON:
        raise InvalidEntitlementGrant(
            f"a grant may end at most {GRANT_MAX_DAYS} days from now"
        )
