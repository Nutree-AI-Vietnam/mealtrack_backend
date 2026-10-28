"""Pure subscription access evaluation over normalized provider facts."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


class SubscriptionFacts(Protocol):
    status: str
    expires_at: datetime | None
    grace_period_expires_at: datetime | None
    period_type: str | None
    provider_snapshot_at: datetime | None
    # Set when a provider event changed this row but its verified snapshot
    # could not be read; cleared by the next snapshot taken at or after it.
    provider_sync_required_at: datetime | None


@dataclass(frozen=True)
class SubscriptionAccess:
    has_access: bool
    status: str
    expires_at: datetime | None
    # The granting row with the latest deadline (a lifetime grant wins); None
    # when access is denied. Lets callers describe the subscription (product,
    # provider, renewal) without re-deciding access.
    source: Any = field(default=None, compare=False, repr=False)


# Providers whose grants are auto-renewing subscriptions. Anything else (a
# fixed-term pass, a manual grant) never renews on its own.
RENEWING_PROVIDERS = frozenset({"revenuecat"})
# RevenueCat names the product of a promotional (support- or API-granted)
# entitlement rc_promo_<entitlement>_<duration>, store 'promotional'. It is a
# fixed-term grant: RevenueCat reports willRenew false for it.
REVENUECAT_PROMOTIONAL_PRODUCT_PREFIX = "rc_promo_"


def subscription_will_renew(record: Any) -> bool:
    """Whether the subscription behind a grant is set to renew.

    Mirrors RevenueCat's EntitlementInfo.willRenew: false once an
    unsubscribe ('cancelled') or a billing issue is detected, for a
    promotional grant, and for a lifetime grant, which has nothing to renew.
    """
    if getattr(record, "provider", None) not in RENEWING_PROVIDERS:
        return False
    if record.expires_at is None:
        return False
    if str(getattr(record, "product_id", "") or "").startswith(
        REVENUECAT_PROMOTIONAL_PRODUCT_PREFIX
    ):
        return False
    return record.status == "active"


def entitlements_payload(access: SubscriptionAccess) -> dict[str, Any]:
    """The GET /v1/me/entitlements body for one access decision.

    product_id, will_renew and provider describe the granting row with the
    latest deadline (a lifetime grant wins); all null when access is denied.
    """
    source = access.source if access.has_access else None
    return {
        "has_access": access.has_access,
        "status": access.status,
        "expires_at": access.expires_at,
        "product_id": source.product_id if source is not None else None,
        "will_renew": subscription_will_renew(source) if source is not None else None,
        "provider": source.provider if source is not None else None,
    }


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Statuses the policy can turn into a grant; 'expired' and 'refunded' never do.
GRANTING_STATUSES = frozenset({"active", "cancelled", "billing_issue"})


def could_still_grant(record: Any, now: datetime | None = None) -> bool:
    """Whether a row would grant access at ``now`` if its facts held.

    The most the policy could grant from the row, ignoring whether a snapshot
    verified it: a granting status that is unexpired (no expiry is a
    lifetime grant) or still in its grace period. A 'cancelled' row whose
    paid period ended long ago never can.
    """
    if record.status not in GRANTING_STATUSES:
        return False
    instant = _utc(now or datetime.now(UTC))
    expires_at = record.expires_at
    grace = getattr(record, "grace_period_expires_at", None)
    return (
        expires_at is None
        or _utc(expires_at) > instant
        or (grace is not None and _utc(grace) > instant)
    )


# Why stored rows leave the policy unable to decide (support and monitoring
# vocabulary). The first three are also the provider markers on a row.
UNRESOLVED_SYNC_REQUIRED = "sync_required"
UNRESOLVED_REFUND_PENDING = "refund_pending"
UNRESOLVED_TRANSFER_SOURCE = "transfer_source"
UNRESOLVED_UNVERIFIED_GRANT = "unverified_grant"
_UNRESOLVED_REASON_ORDER = (
    UNRESOLVED_SYNC_REQUIRED,
    UNRESOLVED_REFUND_PENDING,
    UNRESOLVED_TRANSFER_SOURCE,
    UNRESOLVED_UNVERIFIED_GRANT,
)


def unresolved_access_reasons(subscriptions: Iterable[Any]) -> list[str]:
    """Why evaluate_subscription_access treats some rows as unverified.

    ``sync_required``: a provider event was applied without a verified
    snapshot; ``refund_pending`` or ``transfer_source`` add that the event
    was a refund or moved the purchases away (TRANSFER). ``unverified_grant``:
    a granting row no snapshot ever verified. Each reason once, in that
    order; empty when every row is verified.
    """
    found: set[str] = set()
    for record in subscriptions:
        if getattr(record, "provider_sync_required_at", None) is not None:
            found.add(UNRESOLVED_SYNC_REQUIRED)
            if getattr(record, "provider_refund_pending_at", None) is not None:
                found.add(UNRESOLVED_REFUND_PENDING)
            if getattr(record, "provider_transfer_source_at", None) is not None:
                found.add(UNRESOLVED_TRANSFER_SOURCE)
        elif record.status in GRANTING_STATUSES and record.provider_snapshot_at is None:
            found.add(UNRESOLVED_UNVERIFIED_GRANT)
    return [reason for reason in _UNRESOLVED_REASON_ORDER if reason in found]


def fallback_may_grant(record: Any, now: datetime | None = None) -> bool:
    """Whether the paid-route fallback may grant a row the policy cannot verify.

    ``could_still_grant``, except for a row a refund marked while its
    provider customer could not be read or applied
    (``provider_refund_pending_at``): the refund may have ended the paid
    period the row still shows, so only a snapshot may grant it again.
    """
    if getattr(record, "provider_refund_pending_at", None) is not None:
        return False
    return could_still_grant(record, now)


def evaluate_subscription_access(
    subscriptions: Iterable[SubscriptionFacts], now: datetime | None = None
) -> SubscriptionAccess:
    """Return access only from provider-verified, unexpired normalized facts."""
    instant = _utc(now or datetime.now(UTC))
    records = list(subscriptions)
    grants: list[tuple[str, datetime | None, SubscriptionFacts]] = []
    unresolved = False

    for record in records:
        if getattr(record, "provider_sync_required_at", None) is not None:
            # A provider event was applied without a verified snapshot, so
            # this row's facts are unverified whatever its status says.
            unresolved = True
            continue
        if record.status not in GRANTING_STATUSES:
            continue
        if record.provider_snapshot_at is None:
            unresolved = True
            continue

        expiry = _utc(record.expires_at) if record.expires_at else None
        grace = (
            _utc(record.grace_period_expires_at)
            if record.grace_period_expires_at
            else None
        )
        if record.status == "billing_issue":
            if expiry and expiry > instant:
                status = "trial" if record.period_type == "trial" else "active"
                grants.append((status, expiry, record))
            elif grace and grace > instant:
                grants.append(("grace_period", grace, record))
            continue

        if expiry is None:
            grants.append(("active", None, record))
        elif expiry > instant:
            status = "trial" if record.period_type == "trial" else "active"
            grants.append((status, expiry, record))

    if grants:
        # An explicitly verified lifetime entitlement has no deadline, so it
        # wins; otherwise the grant that lasts longest. Ties keep row order.
        _, expiry, source = max(
            grants,
            key=lambda grant: (grant[1] is None, grant[1] or instant),
        )
        if any(status == "active" for status, _, _ in grants):
            public_status = "active"
        elif any(status == "trial" for status, _, _ in grants):
            public_status = "trial"
        else:
            public_status = "grace_period"
        return SubscriptionAccess(True, public_status, expiry, source)

    if unresolved:
        raise SubscriptionAccessUnavailable("Subscription state is unverified")
    return SubscriptionAccess(False, "expired" if records else "none", None)


class SubscriptionAccessUnavailable(RuntimeError):
    """Stored subscription facts are insufficient for a safe access decision."""
