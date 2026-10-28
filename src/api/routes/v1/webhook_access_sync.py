"""Re-read a RevenueCat customer for a webhook event and apply it to access rows.

Every event whose user can be resolved (except the access-neutral types
below) reads the user's RevenueCat customer and applies it to the database
access rows; new RevenueCat event types are synced by default.

The read happens before the webhook transaction, so no database lock is held
across the HTTP call. The legacy event handlers (rows, referral credit and
revocation, lifecycle and affiliate events) always run. When the customer
cannot be read or applied, the user's RevenueCat rows are left unresolved
(the access policy then answers "unavailable", and mobile falls back to its
RevenueCat client decision), the failure is logged and counted (and sent to
Sentry unless it is a provider outage or rate limit), and the webhook still
succeeds: a repeated delivery could not do better, and answering 5xx after
the side effects would only replay them.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.base_dependencies import (
    capture_access_facts,
    mark_revenuecat_access_unresolved,
    record_snapshot_unavailable,
    sync_revenuecat_access,
)
from src.api.routes.v1.webhook_lookup_parsing import parse_platform, parse_timestamp
from src.domain.ports.subscription_service_port import (
    SNAPSHOT_REASON_IDENTITY_MISMATCH,
    SNAPSHOT_REASON_OWNER_CHANGED,
    SNAPSHOT_REASON_UNEXPECTED,
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

# Event types that never change entitlements. Everything else is synced,
# including types RevenueCat adds later.
ACCESS_NEUTRAL_EVENTS = frozenset(
    {
        "TEST",
        "EXPERIMENT_ENROLLMENT",
        "INVOICE_ISSUANCE",
        "VIRTUAL_CURRENCY_TRANSACTION",
    }
)

ACCESS_SYNCED = "synced"
ACCESS_UNRESOLVED = "access_unresolved"
ACCESS_NOT_SYNCED = "not_synced"
ACCESS_ACCOUNT_DELETED = "account_deleted"


@dataclass(frozen=True)
class AccessRead:
    """The outcome of reading one user's RevenueCat customer for an event."""

    user_id: str
    snapshot: RevenueCatSnapshot | None = None
    error: RevenueCatSnapshotUnavailable | None = None
    absence_is_authoritative: bool = False


def event_syncs_access(event_type: str | None) -> bool:
    return event_type not in ACCESS_NEUTRAL_EVENTS


def account_is_deleted(user: Any) -> bool:
    """Whether the user deleted their account (the only writer of is_active).

    Deletion tombstones users.firebase_uid but keeps the RevenueCat rows,
    whose revenuecat_subscriber_id still resolves the user for events of the
    old uid (a store subscription outlives the account). There is no access
    left to sync: reading RevenueCat under the tombstone is impossible and
    marking the rows would only report every such event as a failure.
    """
    return getattr(user, "is_active", True) is False


def is_refund_event(event: dict) -> bool:
    """RevenueCat has no REFUND type: a refund is a CANCELLATION.

    cancel_reason CUSTOMER_SUPPORT marks a refund issued by the store or
    RevenueCat, and refunds carry a negative price. A refund does not always
    end the subscription, so access still comes from the customer snapshot;
    the classification only labels that sync (rows it revokes read
    'refunded', and the loss of access is expected). The legacy effects are a
    cancellation's: no referral clawback, 'subscription_canceled' published.
    """
    if event.get("type") != "CANCELLATION":
        return False
    if event.get("cancel_reason") == "CUSTOMER_SUPPORT":
        return True
    return any(
        isinstance(event.get(key), int | float)
        and not isinstance(event.get(key), bool)
        and event[key] < 0
        for key in ("price", "price_in_purchased_currency")
    )


def event_time(event: dict) -> datetime:
    """RevenueCat's own time for the event; retries reuse it."""
    timestamp = event.get("event_timestamp_ms")
    if isinstance(timestamp, int | float) and not isinstance(timestamp, bool):
        parsed = parse_timestamp(int(timestamp))
        if parsed is not None:
            return parsed
    return utc_now()


async def read_access(
    subscription_service: Any,
    user: Any,
    identity_ids: Iterable[str],
    *,
    absence_is_authoritative: bool = False,
) -> AccessRead:
    """Read the user's RevenueCat customer by Firebase uid.

    Only when the event itself names that uid: reading another id would
    make RevenueCat create an empty customer for it ("get or create") and
    would describe a different customer than the one the event is about.

    Never raises: it runs before the legacy handlers, so any failure here
    becomes an unresolved read instead of a failed delivery that would
    never reach them.
    """
    user_id = str(user.id)
    firebase_uid = user.firebase_uid
    if not isinstance(firebase_uid, str) or firebase_uid not in set(identity_ids):
        return AccessRead(
            user_id,
            error=RevenueCatSnapshotUnavailable(
                "The event does not name the user's Firebase uid",
                reason=SNAPSHOT_REASON_IDENTITY_MISMATCH,
            ),
        )
    try:
        snapshot = await subscription_service.get_verified_snapshot(firebase_uid)
    except RevenueCatSnapshotUnavailable as error:
        return AccessRead(user_id, error=error)
    except Exception as unexpected:  # never lose the event's side effects
        return AccessRead(
            user_id,
            error=_unexpected("RevenueCat customer could not be read", unexpected),
        )
    return AccessRead(
        user_id, snapshot=snapshot, absence_is_authoritative=absence_is_authoritative
    )


async def apply_access(
    session: AsyncSession,
    user: Any,
    event: dict,
    read: AccessRead | None,
    *,
    refund_event: bool = False,
    facts_before_event: dict[str, dict[str, Any]] | None = None,
    downgrade_expected: bool = False,
    transfer_source: bool = False,
) -> str:
    """Apply the read customer, or leave the user's access unresolved.

    ``downgrade_expected`` marks a loss of access as the event's purpose
    (the source of a TRANSFER), so it is not reported as unexpected.
    ``transfer_source`` (RevenueCat moved this user's purchases away) is
    recorded with the marker when the customer cannot be applied, so a later
    refresh or repair can apply an empty customer for the user.
    """
    event_type = event.get("type")
    event_id = event.get("id")
    if read is None or read.user_id != str(user.id):
        error = RevenueCatSnapshotUnavailable(
            "No customer was read for the event's current owner",
            reason=SNAPSHOT_REASON_OWNER_CHANGED,
        )
    elif read.error is not None:
        error = read.error
    else:
        sync_error = await _sync(
            session,
            user,
            read,
            refund_event,
            event_type,
            event_id,
            facts_before_event,
            downgrade_expected,
        )
        if sync_error is None:
            return ACCESS_SYNCED
        error = sync_error

    record_snapshot_unavailable(
        error,
        source="webhook",
        user_id=user.id,
        event_type=event_type,
        event_id=event_id,
    )
    store = event.get("store")
    await mark_revenuecat_access_unresolved(
        session,
        user,
        since=event_time(event),
        product_id=event.get("product_id"),
        platform=parse_platform(store if isinstance(store, str) else ""),
        is_sandbox=event.get("environment") == "SANDBOX",
        transfer_source=transfer_source,
    )
    return ACCESS_UNRESOLVED


async def _sync(
    session: AsyncSession,
    user: Any,
    read: AccessRead,
    refund_event: bool,
    event_type: str | None,
    event_id: str | None,
    facts_before_event: dict[str, dict[str, Any]] | None,
    downgrade_expected: bool,
) -> RevenueCatSnapshotUnavailable | None:
    try:
        await sync_revenuecat_access(
            session,
            user,
            snapshot=read.snapshot,
            refund_event=refund_event,
            absence_is_authoritative=read.absence_is_authoritative,
            source="webhook",
            event_type=event_type,
            event_id=event_id,
            facts_before_event=facts_before_event,
            downgrade_expected=downgrade_expected,
        )
    except RevenueCatSnapshotUnavailable as unavailable:
        return unavailable
    except SQLAlchemyError:
        # The transaction itself is broken: fail the delivery so RevenueCat
        # retries it from scratch.
        raise
    except Exception as unexpected:  # never lose the event's side effects
        return _unexpected("RevenueCat customer could not be applied", unexpected)
    return None


def _unexpected(message: str, cause: Exception) -> RevenueCatSnapshotUnavailable:
    """Report an unforeseen failure as an unresolved read, keeping its cause."""
    wrapped = RevenueCatSnapshotUnavailable(message, reason=SNAPSHOT_REASON_UNEXPECTED)
    wrapped.__cause__ = cause
    return wrapped


async def capture_facts_for(
    session: AsyncSession, user: Any, read: AccessRead | None
) -> dict[str, dict[str, Any]] | None:
    """Capture row facts before the legacy handlers when a snapshot will apply."""
    if read is None or read.snapshot is None or read.user_id != str(user.id):
        return None
    return await capture_access_facts(session, user.id)
