"""Read models and rules behind the subscription access support API.

Support finds a user, sees their rows and the decision the access policy
takes from them, can read RevenueCat live without writing, resyncs a user
the way the app's own refresh does, and works through the repair queue.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.app.services.subscription_access_flags import (
    database_subscription_access_enabled,
    subscription_enforcement_enabled,
    subscription_enforcement_flag_on,
)
from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    entitlements_payload,
    evaluate_subscription_access,
    unresolved_access_reasons,
)
from src.domain.ports.subscription_access_refresh_port import (
    ProviderAccessPeek,
    SubscriptionAccessRefresh,
)
from src.domain.ports.subscription_access_support_repository_port import (
    SupportSubscriptionRecord,
    SupportUserRecord,
    UnresolvedAccessUser,
    UnresolvedQueueCursor,
)

# ``source`` of support's RevenueCat reads in logs, metrics and Sentry.
SUPPORT_LOOKUP_SOURCE = "support_lookup"
SUPPORT_RESYNC_SOURCE = "support_resync"
# More matches than this for one query is reported as ambiguous.
MAX_LOOKUP_MATCHES = 5

DECISION_GRANTED = "granted"
DECISION_DENIED = "denied"
DECISION_UNRESOLVED = "unresolved"

LIVE_OK = "ok"
LIVE_UNAVAILABLE = "unavailable"
LIVE_NOT_READ = "not_read"

# Why a user cannot be read from RevenueCat (resync refuses, live read skips).
NOT_LIVE_INACTIVE = "user_inactive"
NOT_LIVE_NO_FIREBASE_UID = "no_firebase_uid"


class SupportUserNotFound(LookupError):
    """No account matches the query or id."""


class SupportUserAmbiguous(LookupError):
    """Several accounts match the query (an email differing only in case)."""

    def __init__(self, user_ids: list[str]):
        super().__init__(f"{len(user_ids)} users match the query")
        self.user_ids = user_ids


class SupportUserNotLive(RuntimeError):
    """The account cannot be read from RevenueCat: deleted, deactivated, or
    without a Firebase uid. Reading a tombstoned uid would only create an
    empty RevenueCat customer under it."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class InvalidQueueCursor(ValueError):
    """A repair-queue cursor this API did not hand out."""


@dataclass(frozen=True)
class AccessDecision:
    """The access policy's decision over stored rows, as support sees it.

    ``outcome`` is granted, denied or unresolved. When unresolved, every
    entitlement field is None and ``unresolved_reasons`` says which rows the
    policy could not verify (sync_required, refund_pending, transfer_source,
    unverified_grant).
    """

    outcome: str
    has_access: bool | None = None
    status: str | None = None
    expires_at: datetime | None = None
    product_id: str | None = None
    will_renew: bool | None = None
    provider: str | None = None
    unresolved_reasons: list[str] = field(default_factory=list)


def describe_access_decision(
    rows: Iterable[Any], now: datetime | None = None
) -> AccessDecision:
    """What GET /v1/me/entitlements would answer for these rows, or why it
    could not (it answers 503 ENTITLEMENTS_UNAVAILABLE then)."""
    records = list(rows)
    try:
        access = evaluate_subscription_access(records, now=now)
    except SubscriptionAccessUnavailable:
        return AccessDecision(
            DECISION_UNRESOLVED,
            unresolved_reasons=unresolved_access_reasons(records),
        )
    payload = entitlements_payload(access)
    return AccessDecision(
        DECISION_GRANTED if access.has_access else DECISION_DENIED, **payload
    )


@dataclass(frozen=True)
class AccessFlags:
    """The two cutover flags as this process reads them right now."""

    subscription_access_enabled: bool
    subscription_enforcement_enabled: bool
    # Both on: paid routes answer 402 without access.
    enforcement_active: bool


def current_access_flags() -> AccessFlags:
    return AccessFlags(
        subscription_access_enabled=database_subscription_access_enabled(),
        subscription_enforcement_enabled=subscription_enforcement_flag_on(),
        enforcement_active=subscription_enforcement_enabled(),
    )


def not_live_reason(user: SupportUserRecord) -> str | None:
    """Why RevenueCat must not be read for this account; None when it may."""
    if not user.is_live:
        return NOT_LIVE_INACTIVE
    if not user.firebase_uid:
        return NOT_LIVE_NO_FIREBASE_UID
    return None


@dataclass(frozen=True)
class LiveRead:
    """The outcome of support's read-only RevenueCat read."""

    status: str  # ok | unavailable | not_read
    reason: str | None = None
    retry_after_seconds: float | None = None
    peek: ProviderAccessPeek | None = None


@dataclass(frozen=True)
class SupportAccessView:
    user: SupportUserRecord
    subscriptions: list[SupportSubscriptionRecord]
    decision: AccessDecision
    flags: AccessFlags
    live: LiveRead | None = None


@dataclass(frozen=True)
class SupportResyncOutcome:
    user_id: str
    before: AccessDecision
    after: AccessDecision
    sync: SubscriptionAccessRefresh


@dataclass(frozen=True)
class UnresolvedQueuePage:
    items: list[UnresolvedAccessUser]
    next_cursor: UnresolvedQueueCursor | None
    # The time marker ages are measured at.
    checked_at: datetime


def encode_queue_cursor(cursor: UnresolvedQueueCursor) -> str:
    """An opaque, URL-safe cursor for the next repair-queue page."""
    raw = json.dumps([cursor.oldest_marker_at.isoformat(), cursor.user_id])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_queue_cursor(value: str) -> UnresolvedQueueCursor:
    """Raises InvalidQueueCursor for anything encode_queue_cursor did not make."""
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        marked_at, user_id = json.loads(raw)
        if not isinstance(marked_at, str) or not isinstance(user_id, str):
            raise TypeError("cursor fields must be strings")
        parsed = datetime.fromisoformat(marked_at)
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as error:
        raise InvalidQueueCursor("Invalid repair-queue cursor") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return UnresolvedQueueCursor(parsed, user_id)
