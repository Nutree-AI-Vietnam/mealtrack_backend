"""Normalize verified RevenueCat snapshots into database access facts."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.subscription_access import (
    SubscriptionAccessUnavailable,
    evaluate_subscription_access,
)
from src.domain.ports.subscription_service_port import (
    SNAPSHOT_ERROR_REASONS,
    SNAPSHOT_REASON_ABSENT_WITH_HISTORY,
    SNAPSHOT_REASON_CONFLICTING_FACTS,
    SNAPSHOT_REASON_MALFORMED,
    SNAPSHOT_REASON_OWNER_CHANGED,
    SNAPSHOT_REASON_UNKNOWN,
    SNAPSHOT_REASON_UNSUPPORTED_STORE,
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.observability import capture_exception, capture_message, increment_metric

logger = logging.getLogger(__name__)

REVENUECAT_PROVIDER = "revenuecat"
# Statuses the access policy can turn into a grant.
GRANTING_STATUSES = frozenset({"active", "cancelled", "billing_issue"})
# Events whose whole point is to remove access; a downgrade on them is normal
# (so is one on a refund, which arrives as a CANCELLATION).
EXPECTED_DOWNGRADE_EVENTS = frozenset({"EXPIRATION"})


def _date(value: Any, *, field: str, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise RevenueCatSnapshotUnavailable(
            f"RevenueCat {field} is malformed", reason=SNAPSHOT_REASON_MALFORMED
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RevenueCatSnapshotUnavailable(
            f"RevenueCat {field} is malformed", reason=SNAPSHOT_REASON_MALFORMED
        ) from error
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )


def _platform(store: Any) -> str:
    value = str(store or "").upper()
    if value in {"APP_STORE", "MAC_APP_STORE"}:
        return "ios"
    if value == "PLAY_STORE":
        return "android"
    if value in {"PADDLE", "STRIPE", "PROMOTIONAL", "RC_BILLING"}:
        return "web"
    raise RevenueCatSnapshotUnavailable(
        "RevenueCat store is unsupported", reason=SNAPSHOT_REASON_UNSUPPORTED_STORE
    )


def normalize_standard_access_facts(
    subscriber: dict, now: datetime | None = None
) -> dict | None:
    now = now or utc_now()
    entitlements = subscriber["entitlements"]
    standard = entitlements.get("standard")
    if standard is None:
        return None
    if not isinstance(standard, dict):
        raise RevenueCatSnapshotUnavailable(
            "RevenueCat standard entitlement is malformed",
            reason=SNAPSHOT_REASON_MALFORMED,
        )

    product_id = standard.get("product_identifier")
    if not isinstance(product_id, str) or not product_id:
        raise RevenueCatSnapshotUnavailable(
            "RevenueCat product is missing", reason=SNAPSHOT_REASON_MALFORMED
        )
    subscription = subscriber["subscriptions"].get(product_id)
    if not isinstance(subscription, dict):
        non_subscriptions = subscriber.get("non_subscriptions", {})
        purchases = (
            non_subscriptions.get(product_id, [])
            if isinstance(non_subscriptions, dict)
            else []
        )
        subscription = next(
            (item for item in purchases if isinstance(item, dict)), None
        )
    if not isinstance(subscription, dict):
        raise RevenueCatSnapshotUnavailable(
            "RevenueCat purchase metadata is missing", reason=SNAPSHOT_REASON_MALFORMED
        )

    if "expires_date" not in standard:
        raise RevenueCatSnapshotUnavailable(
            "RevenueCat expiry is missing", reason=SNAPSHOT_REASON_MALFORMED
        )
    expires_at = _date(standard["expires_date"], field="expiry", optional=True)
    purchase_value = subscription.get("purchase_date") or standard.get("purchase_date")
    purchased_at = _date(purchase_value, field="purchase date")
    grace = _date(
        subscription.get("grace_period_expires_date"),
        field="grace expiry",
        optional=True,
    )
    is_sandbox = subscription.get("is_sandbox")
    if not isinstance(is_sandbox, bool):
        raise RevenueCatSnapshotUnavailable(
            "RevenueCat environment is missing", reason=SNAPSHOT_REASON_MALFORMED
        )

    paid_through = expires_at is None or expires_at > now
    in_grace = grace is not None and grace > now
    if subscription.get("billing_issues_detected_at") and (paid_through or in_grace):
        status = "billing_issue"
    elif not paid_through:
        # Includes a refund that lands while a billing flag is still set.
        status = "expired"
    elif subscription.get("unsubscribe_detected_at"):
        status = "cancelled"
    else:
        status = "active"
    return {
        "product_id": product_id,
        "platform": _platform(subscription.get("store")),
        "purchased_at": purchased_at,
        "expires_at": expires_at,
        "grace_period_expires_at": grace,
        "period_type": (
            str(subscription.get("period_type", "")).lower()
            if str(subscription.get("period_type", "")).lower()
            in {"normal", "intro", "trial"}
            else "normal"
        ),
        "status": status,
        "cancelled_at": _date(
            subscription.get("unsubscribe_detected_at"),
            field="cancellation time",
            optional=True,
        ),
        "is_sandbox": is_sandbox,
        "store_transaction_id": _store_transaction_id(subscription),
    }


def _store_transaction_id(purchase: dict) -> str | None:
    """RevenueCat v1 names it store_transaction_id; App Store ids may be numbers."""
    value = purchase.get("store_transaction_id")
    if value is None or isinstance(value, bool) or value == "":
        return None
    return str(value)


def _utc(value: Any) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


ACCESS_GRANTED = "granted"
ACCESS_DENIED = "denied"
ACCESS_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class SubscriptionSyncOutcome:
    """What one snapshot did to a user's RevenueCat access rows."""

    result: str  # applied | unchanged | stale_skipped | absent
    access_before: str
    access_after: str
    rows_revoked: int = 0
    row_created: bool = False
    markers_cleared: int = 0
    standard_missing: bool = False

    @property
    def downgraded(self) -> bool:
        return (
            self.access_before == ACCESS_GRANTED and self.access_after == ACCESS_DENIED
        )


def _access_label(rows: list[Subscription]) -> str:
    try:
        granted = evaluate_subscription_access(rows).has_access  # type: ignore[arg-type]
    except SubscriptionAccessUnavailable:
        return ACCESS_UNRESOLVED
    return ACCESS_GRANTED if granted else ACCESS_DENIED


async def lock_subscription_owner(session: AsyncSession, user_id: Any) -> User | None:
    """Serialise every RevenueCat access write for one user on the user row.

    Concurrent webhooks, refreshes and reconcile runs for the same user queue
    behind this lock instead of failing an optimistic check; ordinary writes
    to the users row (last_accessed, onboarding, ...) never conflict with it.
    """
    # populate_existing refreshes the identity-mapped user; flush first so no
    # pending change on it is discarded.
    await session.flush()
    return await session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _locked_revenuecat_rows(
    session: AsyncSession, user_id: Any
) -> list[Subscription]:
    # The session does not autoflush: make rows a legacy handler just added
    # or changed in this transaction visible to the SELECT below.
    await session.flush()
    rows = await session.scalars(
        select(Subscription)
        .where(
            Subscription.user_id == user_id,
            Subscription.provider == REVENUECAT_PROVIDER,
        )
        .with_for_update()
    )
    return list(rows.all())


def _clear_markers(rows: list[Subscription], request_date: datetime | None) -> int:
    """Clear sync-required markers this snapshot is new enough to resolve."""
    if request_date is None:
        return 0
    cleared = 0
    for row in rows:
        marker = row.provider_sync_required_at
        if marker is not None and _utc(marker) <= request_date:
            setattr(row, "provider_sync_required_at", None)  # noqa: B010
            cleared += 1
    return cleared


# Columns a legacy event handler may write on a row the snapshot sync owns.
ACCESS_FACT_COLUMNS = (
    "product_id",
    "platform",
    "status",
    "purchased_at",
    "expires_at",
    "grace_period_expires_at",
    "period_type",
    "cancelled_at",
    "store_transaction_id",
    "provider_snapshot_at",
    "provider_sync_required_at",
)


async def capture_access_facts(
    session: AsyncSession, user_id: Any
) -> dict[str, dict[str, Any]]:
    """Record the user's RevenueCat row facts before legacy handlers run.

    Passed back to sync_revenuecat_access, it lets the sync undo what a
    legacy event handler wrote on rows a verified snapshot already owned
    (a late or stale event must not overwrite verified facts).
    """
    rows = await _locked_revenuecat_rows(session, user_id)
    return {
        str(row.id): {column: getattr(row, column) for column in ACCESS_FACT_COLUMNS}
        for row in rows
    }


def _restore_verified_facts(
    rows: list[Subscription], facts_before_event: dict[str, dict[str, Any]] | None
) -> int:
    restored = 0
    for row in rows:
        before = (facts_before_event or {}).get(str(row.id))
        if (
            before is None
            or before["provider_snapshot_at"] is None
            or before["provider_sync_required_at"] is not None
        ):
            continue  # not a row a verified snapshot owned
        changed = False
        for column, value in before.items():
            if getattr(row, column) != value:
                setattr(row, column, value)
                changed = True
        restored += changed
    return restored


def _same_snapshot_consistent(
    existing: list[Subscription], facts: dict | None, latest: datetime, revoked: str
) -> bool:
    matching = [
        row
        for row in existing
        if row.provider_snapshot_at is not None
        and _utc(row.provider_snapshot_at) == latest
    ]
    if facts:
        return len(matching) == 1 and all(
            getattr(matching[0], key) == value
            for key, value in facts.items()
            if not (key == "store_transaction_id" and value is None)
        )
    return all(row.status in {revoked, "expired", "refunded"} for row in matching)


async def sync_revenuecat_access(
    session: AsyncSession,
    user: User,
    *,
    adapter: Any | None = None,
    refund_event: bool = False,
    snapshot: RevenueCatSnapshot | None = None,
    source: str = "unknown",
    event_type: str | None = None,
    event_id: str | None = None,
    absence_is_authoritative: bool = False,
    facts_before_event: dict[str, dict[str, Any]] | None = None,
) -> SubscriptionSyncOutcome:
    """Apply one verified provider snapshot to the user's RevenueCat rows.

    Must run inside the caller's unit of work: it locks the user row, so
    writers for one user are serialised, and never commits. Raises
    RevenueCatSnapshotUnavailable (with a reason) before mutating anything
    when the snapshot cannot be applied safely; callers then leave the rows
    unresolved with mark_revenuecat_access_unresolved.

    An empty RevenueCat customer normally proves nothing about rows that
    could still grant access. ``absence_is_authoritative`` is for callers
    that know why it is empty -- the source of a TRANSFER, whose purchases
    RevenueCat just moved away -- and applies it as "no purchases".

    ``facts_before_event`` (from capture_access_facts) undoes legacy handler
    writes on rows a verified snapshot already owned, so only snapshots
    change verified facts -- also when this snapshot is too old to apply.
    """
    if snapshot is None:
        if adapter is None:
            raise RevenueCatSnapshotUnavailable("RevenueCat adapter is required")
        snapshot = await adapter.get_verified_snapshot(user.firebase_uid)
    locked_user = await lock_subscription_owner(session, user.id)
    if locked_user is None:
        raise RevenueCatSnapshotUnavailable(
            "Subscription owner disappeared", reason=SNAPSHOT_REASON_OWNER_CHANGED
        )
    if (
        snapshot.app_user_id is not None
        and snapshot.app_user_id != locked_user.firebase_uid
    ):
        raise RevenueCatSnapshotUnavailable(
            "Subscription owner changed during sync",
            reason=SNAPSHOT_REASON_OWNER_CHANGED,
        )
    existing = await _locked_revenuecat_rows(session, locked_user.id)
    access_before = _access_label(existing)
    request_date = _utc(snapshot.request_date) if snapshot.request_date else None

    authoritative_absence = (
        absence_is_authoritative
        and snapshot.subscriber is not None
        and request_date is not None
    )
    if snapshot.absent and not authoritative_absence:
        if any(row.status in GRANTING_STATUSES for row in existing):
            # RevenueCat holds no purchases under this Firebase uid, yet the
            # database holds a possible grant: it came from another identity
            # (fallback match, manual grant). Never expire it on absence.
            raise RevenueCatSnapshotUnavailable(
                "RevenueCat absence cannot verify existing subscription history",
                reason=SNAPSHOT_REASON_ABSENT_WITH_HISTORY,
            )
        markers_cleared = _clear_markers(existing, request_date)
        outcome = SubscriptionSyncOutcome(
            "absent",
            access_before,
            _access_label(existing),
            markers_cleared=markers_cleared,
        )
        _record_sync_outcome(
            outcome, locked_user, source, event_type, event_id, refund_event
        )
        return outcome

    if snapshot.subscriber is None or request_date is None:
        raise RevenueCatSnapshotUnavailable(
            "RevenueCat snapshot is incomplete", reason=SNAPSHOT_REASON_MALFORMED
        )
    entitlements = snapshot.subscriber.get("entitlements") or {}
    facts = normalize_standard_access_facts(snapshot.subscriber)
    standard_missing = "standard" not in entitlements and bool(entitlements)
    if facts and facts["status"] == "expired":
        facts = None
    revoked_status = "refunded" if refund_event else "expired"
    latest_snapshot = max(
        (
            _utc(row.provider_snapshot_at)
            for row in existing
            if row.provider_snapshot_at is not None
        ),
        default=datetime.min.replace(tzinfo=UTC),
    )
    if request_date < latest_snapshot:
        _restore_verified_facts(existing, facts_before_event)
        outcome = SubscriptionSyncOutcome(
            "stale_skipped", access_before, _access_label(existing)
        )
        _record_sync_outcome(
            outcome, locked_user, source, event_type, event_id, refund_event
        )
        return outcome
    if request_date == latest_snapshot:
        # The same RevenueCat read applied twice (e.g. two deliveries that
        # queued on the lock): compare against the verified facts, not what
        # this event's legacy handler just wrote.
        _restore_verified_facts(existing, facts_before_event)
        if not _same_snapshot_consistent(
            existing, facts, latest_snapshot, revoked_status
        ):
            raise RevenueCatSnapshotUnavailable(
                "RevenueCat returned conflicting facts at the same snapshot time",
                reason=SNAPSHOT_REASON_CONFLICTING_FACTS,
            )
        if all(
            row.provider_snapshot_at is not None
            and row.provider_sync_required_at is None
            for row in existing
        ):
            outcome = SubscriptionSyncOutcome("unchanged", access_before, access_before)
            _record_sync_outcome(
                outcome, locked_user, source, event_type, event_id, refund_event
            )
            return outcome

    # Everything below mutates; nothing below raises.
    _restore_verified_facts(existing, facts_before_event)
    target_row = (
        next((row for row in existing if row.product_id == facts["product_id"]), None)
        if facts
        else None
    )
    rows_revoked = 0
    for row in existing:
        if row is not target_row and row.status in GRANTING_STATUSES:
            setattr(row, "status", revoked_status)  # noqa: B010
            rows_revoked += 1
        if row is not target_row:
            setattr(row, "grace_period_expires_at", None)  # noqa: B010
        setattr(row, "provider_snapshot_at", request_date)  # noqa: B010

    row_created = False
    if facts:
        if target_row is None:
            target_row = Subscription(
                id=str(uuid4()),
                user_id=locked_user.id,
                revenuecat_subscriber_id=locked_user.firebase_uid,
                product_id=facts["product_id"],
                platform=facts["platform"],
                purchased_at=facts["purchased_at"],
                status=facts["status"],
                is_sandbox=facts["is_sandbox"],
            )
            session.add(target_row)
            existing.append(target_row)
            row_created = True
        for key, value in facts.items():
            if key == "store_transaction_id" and value is None:
                # Never blank an id a purchase webhook already stored.
                continue
            setattr(target_row, key, value)
        setattr(target_row, "provider", REVENUECAT_PROVIDER)  # noqa: B010
        setattr(  # noqa: B010
            target_row, "revenuecat_subscriber_id", locked_user.firebase_uid
        )
        setattr(target_row, "provider_snapshot_at", request_date)  # noqa: B010

    markers_cleared = _clear_markers(existing, request_date)
    outcome = SubscriptionSyncOutcome(
        "applied",
        access_before,
        _access_label(existing),
        rows_revoked=rows_revoked,
        row_created=row_created,
        markers_cleared=markers_cleared,
        standard_missing=standard_missing,
    )
    _record_sync_outcome(
        outcome, locked_user, source, event_type, event_id, refund_event
    )
    return outcome


async def mark_revenuecat_access_unresolved(
    session: AsyncSession,
    user: User,
    *,
    since: datetime | None,
    product_id: str | None = None,
    platform: str | None = None,
    is_sandbox: bool = False,
) -> int:
    """Leave the user's RevenueCat access unresolved until a snapshot repairs it.

    Used when a provider event was applied (or may have changed access) but
    its verified snapshot could not be read. ``since`` is RevenueCat's time
    for the event (``event_timestamp_ms``): only a snapshot taken at or after
    it clears the marker, so an older snapshot racing in cannot. A user with
    no RevenueCat row gets a non-granting placeholder row carrying the
    marker, so the policy answers "unresolved" instead of "none".
    Returns the number of rows marked.
    """
    locked_user = await lock_subscription_owner(session, user.id)
    if locked_user is None:
        return 0
    rows = await _locked_revenuecat_rows(session, locked_user.id)
    marker = _utc(since) if since is not None else utc_now()
    if not rows:
        placeholder = Subscription(
            id=str(uuid4()),
            user_id=locked_user.id,
            revenuecat_subscriber_id=locked_user.firebase_uid,
            provider=REVENUECAT_PROVIDER,
            product_id=product_id or "unknown",
            platform=platform or "ios",
            # Non-granting for the legacy (flag-off) check; the marker makes
            # the access policy treat it as unresolved.
            status="expired",
            purchased_at=marker,
            is_sandbox=is_sandbox,
        )
        session.add(placeholder)
        rows = [placeholder]
    for row in rows:
        current = row.provider_sync_required_at
        setattr(  # noqa: B010
            row,
            "provider_sync_required_at",
            marker if current is None else max(_utc(current), marker),
        )
    return len(rows)


def record_snapshot_unavailable(
    error: RevenueCatSnapshotUnavailable,
    *,
    source: str,
    user_id: Any = None,
    event_type: str | None = None,
    event_id: str | None = None,
) -> None:
    """Log, count and report a provider state that could not be verified."""
    reason = getattr(error, "reason", SNAPSHOT_REASON_UNKNOWN)
    context = {
        "component": "subscription_access",
        "operation": "revenuecat_snapshot",
        "source": source,
        "user_id": str(user_id) if user_id is not None else None,
        "event_type": event_type,
        "event_id": event_id,
        "failure_kind": reason,
        "result": "access_unresolved",
    }
    logger.warning(
        "RevenueCat snapshot unavailable; access left unresolved for repair: "
        "user_id=%s source=%s event_type=%s event_id=%s reason=%s error=%s",
        user_id,
        source,
        event_type,
        event_id,
        reason,
        error,
    )
    increment_metric(
        "subscription_access.snapshot_unavailable",
        attributes={
            "source": source,
            "failure_kind": reason,
            "event_type": event_type or "none",
        },
    )
    if reason in SNAPSHOT_ERROR_REASONS:
        # Contract or configuration bug: needs engineering attention now.
        capture_exception(error, context=context)
    else:
        capture_message(
            "RevenueCat snapshot unavailable", level="warning", context=context
        )


def _record_sync_outcome(
    outcome: SubscriptionSyncOutcome,
    user: User,
    source: str,
    event_type: str | None,
    event_id: str | None,
    refund_event: bool,
) -> None:
    attributes = {
        "source": source,
        "event_type": event_type or "none",
        "result": outcome.result,
    }
    increment_metric("subscription_access.sync", attributes=attributes)
    context = {
        "component": "subscription_access",
        "operation": "revenuecat_sync",
        "user_id": str(user.id),
        "event_id": event_id,
        **attributes,
    }
    if outcome.result == "stale_skipped":
        logger.warning(
            "RevenueCat snapshot older than stored facts skipped: user_id=%s "
            "source=%s event_type=%s event_id=%s",
            user.id,
            source,
            event_type,
            event_id,
        )
        capture_message(
            "RevenueCat snapshot older than stored facts skipped",
            level="warning",
            context=context,
        )
        return
    if outcome.standard_missing:
        logger.warning(
            "RevenueCat customer has entitlements but no 'standard': user_id=%s "
            "source=%s event_type=%s",
            user.id,
            source,
            event_type,
        )
        capture_message(
            "RevenueCat customer has entitlements but no 'standard'",
            level="warning",
            context=context,
        )
    if outcome.downgraded:
        increment_metric(
            "subscription_access.downgrade",
            value=max(outcome.rows_revoked, 1),
            attributes=attributes,
        )
        if refund_event or event_type in EXPECTED_DOWNGRADE_EVENTS:
            logger.info(
                "RevenueCat sync removed access: user_id=%s source=%s "
                "event_type=%s event_id=%s rows_revoked=%d",
                user.id,
                source,
                event_type,
                event_id,
                outcome.rows_revoked,
            )
        else:
            logger.warning(
                "RevenueCat sync removed access on an unexpected event: "
                "user_id=%s source=%s event_type=%s event_id=%s rows_revoked=%d",
                user.id,
                source,
                event_type,
                event_id,
                outcome.rows_revoked,
            )
            capture_message(
                "RevenueCat sync removed access on an unexpected event",
                level="warning",
                context=context,
            )
        return
    logger.info(
        "RevenueCat access synced: user_id=%s source=%s event_type=%s "
        "event_id=%s result=%s before=%s after=%s rows_revoked=%d "
        "row_created=%s markers_cleared=%d",
        user.id,
        source,
        event_type,
        event_id,
        outcome.result,
        outcome.access_before,
        outcome.access_after,
        outcome.rows_revoked,
        outcome.row_created,
        outcome.markers_cleared,
    )
