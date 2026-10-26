"""Pure subscription access evaluation over normalized provider facts."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol


class SubscriptionFacts(Protocol):
    status: str
    expires_at: datetime | None
    grace_period_expires_at: datetime | None
    period_type: str | None
    provider_snapshot_at: datetime | None


@dataclass(frozen=True)
class SubscriptionAccess:
    has_access: bool
    status: str
    expires_at: datetime | None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def evaluate_subscription_access(
    subscriptions: Iterable[SubscriptionFacts], now: datetime | None = None
) -> SubscriptionAccess:
    """Return access only from provider-verified, unexpired normalized facts."""
    instant = _utc(now or datetime.now(UTC))
    records = list(subscriptions)
    grants: list[tuple[str, datetime | None]] = []
    unresolved = False

    for record in records:
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
                grants.append((status, expiry))
            elif grace and grace > instant:
                grants.append(("grace_period", grace))
            continue

        if expiry is None:
            grants.append(("active", None))
        elif expiry > instant:
            status = "trial" if record.period_type == "trial" else "active"
            grants.append((status, expiry))

    if grants:
        # An explicitly verified lifetime entitlement has no deadline.
        if any(deadline is None for _, deadline in grants):
            expiry = None
        else:
            expiry = max(deadline for _, deadline in grants if deadline is not None)
        if any(status == "active" for status, _ in grants):
            public_status = "active"
        elif any(status == "trial" for status, _ in grants):
            public_status = "trial"
        else:
            public_status = "grace_period"
        return SubscriptionAccess(True, public_status, expiry)

    if unresolved:
        raise SubscriptionAccessUnavailable("Subscription state is unverified")
    return SubscriptionAccess(False, "expired" if records else "none", None)


class SubscriptionAccessUnavailable(RuntimeError):
    """Stored subscription facts are insufficient for a safe access decision."""
