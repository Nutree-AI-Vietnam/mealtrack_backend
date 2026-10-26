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


def subscription_will_renew(record: Any) -> bool:
    """Whether the subscription behind a grant is set to renew.

    Mirrors RevenueCat's EntitlementInfo.willRenew: false once an
    unsubscribe ('cancelled') or a billing issue is detected, and for a
    lifetime grant, which has nothing to renew.
    """
    if getattr(record, "provider", None) not in RENEWING_PROVIDERS:
        return False
    if record.expires_at is None:
        return False
    return record.status == "active"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


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
        if record.status not in {"active", "cancelled", "billing_issue"}:
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
