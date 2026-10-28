"""Normalize verified RevenueCat snapshots into database access facts."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.model.subscription_access import (
    GRANTING_STATUSES,
    SubscriptionAccessUnavailable,
    could_still_grant,
    evaluate_subscription_access,
    subscription_will_renew,
)
from src.domain.ports.subscription_access_refresh_port import (
    ProviderAccessPeek,
    ProviderEntitlement,
)
from src.domain.ports.subscription_service_port import (
    SNAPSHOT_ERROR_REASONS,
    SNAPSHOT_REASON_ABSENT_WITH_HISTORY,
    SNAPSHOT_REASON_CONFLICTING_FACTS,
    SNAPSHOT_REASON_MALFORMED,
    SNAPSHOT_REASON_OWNER_CHANGED,
    SNAPSHOT_REASON_UNKNOWN,
    SNAPSHOT_REASON_UNSUPPORTED_STORE,
    SNAPSHOT_TRANSIENT_REASONS,
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User
from src.infra.repositories.subscription_repository_async import product_match_rank
from src.observability import capture_exception, capture_message, increment_metric
from src.subscription_access_alerts import (
    ALERT_SNAPSHOT_FAILURE,
    ALERT_STANDARD_MISSING,
    ALERT_UNEXPECTED_DOWNGRADE,
    alert_fingerprint,
)

logger = logging.getLogger(__name__)

REVENUECAT_PROVIDER = "revenuecat"
# Events whose whole point is to remove access; a downgrade on them is normal
# (so is one on a refund, which arrives as a CANCELLATION, and one on the
# source of a TRANSFER: callers say so with ``downgrade_expected``).
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


def describe_provider_access(
    snapshot: RevenueCatSnapshot, now: datetime | None = None
) -> ProviderAccessPeek:
    """What one RevenueCat read says about 'standard', without applying it.

    The entitlement is normalized exactly as the sync would store it, so a
    support read shows what a resync would write. Raises
    RevenueCatSnapshotUnavailable (malformed, unsupported_store) when the
    sync could not apply the customer either.
    """
    read_at = _utc(snapshot.request_date) if snapshot.request_date else None
    subscriber = snapshot.subscriber
    if snapshot.absent or subscriber is None:
        return ProviderAccessPeek(read_at, absent=True, entitlements=(), standard=None)
    entitlements = subscriber.get("entitlements") or {}
    facts = normalize_standard_access_facts(subscriber, now)
    standard = None
    if facts is not None:
        renewal = SimpleNamespace(provider=REVENUECAT_PROVIDER, **facts)
        standard = ProviderEntitlement(
            has_access=facts["status"] != "expired",
            status=facts["status"],
            product_id=facts["product_id"],
            platform=facts["platform"],
            purchased_at=facts["purchased_at"],
            expires_at=facts["expires_at"],
            grace_period_expires_at=facts["grace_period_expires_at"],
            period_type=facts["period_type"],
            is_sandbox=facts["is_sandbox"],
            will_renew=subscription_will_renew(renewal),
        )
    return ProviderAccessPeek(
        read_at,
        absent=False,
        entitlements=tuple(sorted(str(name) for name in entitlements)),
        standard=standard,
    )


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


def _access_label(rows: list[Any]) -> str:
    try:
        granted = evaluate_subscription_access(rows).has_access
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
        # Newest first, like the repository's row lookup: the order
        # _row_for_product breaks a tie in.
        .order_by(Subscription.purchased_at.desc(), Subscription.created_at.desc())
        .with_for_update()
        # A row loaded before the lock (the webhook's user lookup can match
        # through a subscription row) keeps its pre-lock values in the
        # identity map otherwise, hiding what a concurrent writer committed.
        # The flush above means no pending change of ours is discarded.
        .execution_options(populate_existing=True)
    )
    return list(rows.all())


def clear_sync_markers(rows: list[Subscription], request_date: datetime | None) -> int:
    """Clear sync-required markers a snapshot taken at ``request_date`` resolves.

    Only for rows that snapshot was applied to: a marker set for an event at
    or before the snapshot time is answered by it, and so is a TRANSFER or a
    refund the rows were left unresolved by (``provider_transfer_source_at``,
    ``provider_refund_pending_at``). Returns the number of sync-required
    markers cleared.
    """
    if request_date is None:
        return 0
    cleared = 0
    for row in rows:
        marker = row.provider_sync_required_at
        if marker is not None and _utc(marker) <= request_date:
            setattr(row, "provider_sync_required_at", None)  # noqa: B010
            cleared += 1
        transfer = getattr(row, "provider_transfer_source_at", None)
        if transfer is not None and _utc(transfer) <= request_date:
            setattr(row, "provider_transfer_source_at", None)  # noqa: B010
        refund = getattr(row, "provider_refund_pending_at", None)
        if refund is not None and _utc(refund) <= request_date:
            setattr(row, "provider_refund_pending_at", None)  # noqa: B010
    return cleared


def _transfer_source_pending(
    rows: list[Subscription], request_date: datetime | None
) -> bool:
    """Whether a snapshot at ``request_date`` follows a TRANSFER away from
    the user that no snapshot has answered yet.

    RevenueCat moved the user's purchases to another app user id at that
    time, so a customer read at or after it that holds nothing is RevenueCat
    saying so, not missing history.
    """
    if request_date is None:
        return False
    moved = [getattr(row, "provider_transfer_source_at", None) for row in rows]
    return any(at is not None and _utc(at) <= request_date for at in moved)


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


def _access_before_event(
    rows: list[Subscription], facts_before_event: dict[str, dict[str, Any]] | None
) -> str:
    """The user's access before this event's legacy handler touched the rows.

    A webhook runs its legacy handler (EXPIRATION -> 'expired', RENEWAL ->
    'active', ...) in the same transaction before the sync, so the rows no
    longer show what the user had. The captured facts do; rows the handler
    created did not exist yet.
    """
    if facts_before_event is None:
        return _access_label(rows)
    return _access_label(
        [SimpleNamespace(**facts) for facts in facts_before_event.values()]
    )


# (row, column, value the legacy handler wrote) for each restored column.
LegacyWrites = list[tuple[Subscription, str, Any]]


def _restore_verified_facts(
    rows: list[Subscription], facts_before_event: dict[str, dict[str, Any]] | None
) -> LegacyWrites:
    """Undo legacy handler writes on rows a verified snapshot owned.

    Returns what was overwritten, so the writes can be put back when the
    snapshot turns out not to be applicable.
    """
    overwritten: LegacyWrites = []
    for row in rows:
        before = (facts_before_event or {}).get(str(row.id))
        if (
            before is None
            or before["provider_snapshot_at"] is None
            or before["provider_sync_required_at"] is not None
        ):
            continue  # not a row a verified snapshot owned
        for column, value in before.items():
            current = getattr(row, column)
            if current != value:
                overwritten.append((row, column, current))
                setattr(row, column, value)
    return overwritten


def _row_for_product(
    rows: list[Subscription], product_id: str, platform: str
) -> Subscription | None:
    """The row holding the snapshot's ``product_id`` on its ``platform``.

    Candidates hold the exact id or the same store product (a Google Play row
    a legacy handler wrote as '<subscription>:<base plan>' for the bare id a
    snapshot names), so a snapshot updates that row instead of adding a
    duplicate next to it. A row of the snapshot's own store wins first: an
    App Store product may share its id with the Play subscription, and the
    Play webhook named the Play row. Then the exact id, then the newest row
    (``rows`` come newest first, as _locked_revenuecat_rows orders them).
    """
    ranked = [
        (str(row.platform) != platform, rank, index, row)
        for index, row in enumerate(rows)
        if (rank := product_match_rank(str(row.product_id), product_id)) < 2
    ]
    return min(ranked, key=lambda item: item[:3])[3] if ranked else None


def _put_back(legacy_writes: LegacyWrites) -> None:
    for row, column, value in legacy_writes:
        setattr(row, column, value)


def _could_still_grant(row: Subscription, instant: datetime) -> bool:
    """Whether a row could grant access at ``instant`` if its facts held.

    A granting status alone is not enough: a 'cancelled' row whose paid
    period ended long ago (an unreconciled legacy row) can never grant.
    """
    return could_still_grant(row, instant)


def _same_snapshot_consistent(
    existing: list[Subscription], facts: dict | None, latest: datetime
) -> bool:
    """Whether the rows stamped at ``latest`` are what applying ``facts`` wrote.

    Applying a snapshot stamps every RevenueCat row with its time: the row
    for the entitled product carries its facts and every other row is left
    non-granting. A different read with the same time cannot be ordered
    against the one applied.
    """
    stamped = [
        row
        for row in existing
        if row.provider_snapshot_at is not None
        and _utc(row.provider_snapshot_at) == latest
    ]
    holders = [
        row
        for row in stamped
        if facts
        and all(
            getattr(row, key) == value
            for key, value in facts.items()
            if not (key == "store_transaction_id" and value is None)
        )
    ]
    others = [row for row in stamped if all(row is not held for held in holders)]
    return len(holders) == (1 if facts else 0) and all(
        row.status not in GRANTING_STATUSES for row in others
    )


@dataclass(frozen=True)
class _SyncPlan:
    """What a snapshot will do, decided before any row changes."""

    result: str  # apply | absent | stale_skipped | unchanged
    snapshot_at: datetime = datetime.min.replace(tzinfo=UTC)
    facts: dict | None = None
    standard_missing: bool = False


def _plan_sync(
    existing: list[Subscription],
    snapshot: RevenueCatSnapshot,
    request_date: datetime | None,
    *,
    revoked_status: str,
    absence_is_authoritative: bool,
) -> _SyncPlan:
    """Decide what the snapshot does; raise when it cannot be applied safely.

    Reads ``existing`` (with verified facts already restored) and never
    changes it.
    """
    authoritative_absence = (
        absence_is_authoritative
        and snapshot.subscriber is not None
        and request_date is not None
    )
    if snapshot.absent and not authoritative_absence:
        instant = request_date or utc_now()
        if any(_could_still_grant(row, instant) for row in existing):
            # RevenueCat holds no purchases under this Firebase uid, yet the
            # database holds a possible grant: it came from another identity
            # (fallback match, manual grant). Never expire it on absence.
            raise RevenueCatSnapshotUnavailable(
                "RevenueCat absence cannot verify existing subscription history",
                reason=SNAPSHOT_REASON_ABSENT_WITH_HISTORY,
            )
        return _SyncPlan("absent")

    if snapshot.subscriber is None or request_date is None:
        raise RevenueCatSnapshotUnavailable(
            "RevenueCat snapshot is incomplete", reason=SNAPSHOT_REASON_MALFORMED
        )
    entitlements = snapshot.subscriber.get("entitlements") or {}
    facts = normalize_standard_access_facts(snapshot.subscriber)
    standard_missing = "standard" not in entitlements and bool(entitlements)
    if facts and facts["status"] == "expired":
        facts = None
    latest_snapshot = max(
        (
            _utc(row.provider_snapshot_at)
            for row in existing
            if row.provider_snapshot_at is not None
        ),
        default=datetime.min.replace(tzinfo=UTC),
    )
    if request_date < latest_snapshot:
        return _SyncPlan("stale_skipped")
    if request_date == latest_snapshot:
        # The same RevenueCat read applied twice (e.g. two deliveries that
        # queued on the lock).
        if not _same_snapshot_consistent(existing, facts, latest_snapshot):
            raise RevenueCatSnapshotUnavailable(
                "RevenueCat returned conflicting facts at the same snapshot time",
                reason=SNAPSHOT_REASON_CONFLICTING_FACTS,
            )
        if all(
            row.provider_snapshot_at is not None
            and row.provider_sync_required_at is None
            for row in existing
        ):
            return _SyncPlan("unchanged")
    return _SyncPlan(
        "apply",
        snapshot_at=request_date,
        facts=facts,
        standard_missing=standard_missing,
    )


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
    downgrade_expected: bool = False,
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
    RevenueCat just moved away -- and applies it as "no purchases". A TRANSFER
    source whose own read failed is remembered on its rows
    (``provider_transfer_source_at``), so any later snapshot taken after the
    transfer -- a refresh, a repair, the next webhook -- applies it the same
    way.

    ``facts_before_event`` (from capture_access_facts) undoes legacy handler
    writes on rows a verified snapshot already owned, so only snapshots
    change verified facts -- also when this snapshot is too old to apply.
    It is also the "before" of the outcome: what the user had before the
    event, not what the event's legacy handler wrote.

    A sync that removes access is reported as unexpected unless it comes
    from an EXPIRATION, a refund, or a caller that passes
    ``downgrade_expected`` (the source of a TRANSFER loses what RevenueCat
    moved away).
    """
    downgrade_expected = (
        downgrade_expected or refund_event or event_type in EXPECTED_DOWNGRADE_EVENTS
    )
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
    access_before = _access_before_event(existing, facts_before_event)
    request_date = _utc(snapshot.request_date) if snapshot.request_date else None
    revoked_status = "refunded" if refund_event else "expired"
    if _transfer_source_pending(existing, request_date):
        # What this snapshot removes is what the transfer moved away.
        absence_is_authoritative = True
        downgrade_expected = True

    # Judge the snapshot against the verified facts, not against what this
    # event's legacy handler just wrote on them. If the snapshot cannot be
    # applied, the event's own writes stand (the caller then leaves the rows
    # unresolved), exactly as when RevenueCat could not be read at all.
    legacy_writes = _restore_verified_facts(existing, facts_before_event)
    try:
        plan = _plan_sync(
            existing,
            snapshot,
            request_date,
            revoked_status=revoked_status,
            absence_is_authoritative=absence_is_authoritative,
        )
    except Exception:
        _put_back(legacy_writes)
        raise

    if plan.result in {"stale_skipped", "unchanged"}:
        outcome = SubscriptionSyncOutcome(
            plan.result, access_before, _access_label(existing)
        )
        _record_sync_outcome(
            outcome, locked_user, source, event_type, event_id, downgrade_expected
        )
        return outcome

    if plan.result == "absent":
        # Nothing under this uid, and no row that could still grant: rows
        # whose paid period is over (unreconciled legacy rows, typically)
        # resolve to "no access" instead of staying unresolved forever.
        rows_revoked = 0
        for row in existing:
            stamped = row.provider_snapshot_at
            if request_date is None or (
                stamped is not None and _utc(stamped) >= request_date
            ):
                continue  # never move verified facts back in time
            if row.status in GRANTING_STATUSES:
                setattr(row, "status", "expired")  # noqa: B010
                rows_revoked += 1
            setattr(row, "provider_snapshot_at", request_date)  # noqa: B010
        markers_cleared = clear_sync_markers(existing, request_date)
        outcome = SubscriptionSyncOutcome(
            "absent",
            access_before,
            _access_label(existing),
            rows_revoked=rows_revoked,
            markers_cleared=markers_cleared,
        )
        _record_sync_outcome(
            outcome, locked_user, source, event_type, event_id, downgrade_expected
        )
        return outcome

    # Everything below mutates; nothing below raises.
    request_date = plan.snapshot_at
    facts = plan.facts
    standard_missing = plan.standard_missing
    target_row = (
        _row_for_product(existing, facts["product_id"], facts["platform"])
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
                provider=REVENUECAT_PROVIDER,
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

    markers_cleared = clear_sync_markers(existing, request_date)
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
        outcome, locked_user, source, event_type, event_id, downgrade_expected
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
    transfer_source: bool = False,
    refund: bool = False,
) -> int:
    """Leave the user's RevenueCat access unresolved until a snapshot repairs it.

    Used when a provider event was applied (or may have changed access) but
    its verified snapshot could not be read. ``since`` is RevenueCat's time
    for the event (``event_timestamp_ms``): only a snapshot taken at or after
    it clears the marker, so an older snapshot racing in cannot. A user with
    no RevenueCat row gets a non-granting placeholder row carrying the
    marker, so the policy answers "unresolved" instead of "none".
    ``transfer_source`` (the user is the source of a TRANSFER at ``since``)
    also records that, so a later empty customer is applied as authoritative
    (see sync_revenuecat_access). ``refund`` (the event is a refund) also
    records that (``provider_refund_pending_at``), so the paid-route
    fallback never grants the rows on the 'cancelled' status the
    cancellation handler wrote; the snapshot that clears the marker decides.
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
        if transfer_source:
            moved = row.provider_transfer_source_at
            setattr(  # noqa: B010
                row,
                "provider_transfer_source_at",
                marker if moved is None else max(_utc(moved), marker),
            )
        if refund:
            refunded = row.provider_refund_pending_at
            setattr(  # noqa: B010
                row,
                "provider_refund_pending_at",
                marker if refunded is None else max(_utc(refunded), marker),
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
    """Log and count a provider state that could not be verified.

    Also reported to Sentry, except a provider outage or rate limit
    (SNAPSHOT_TRANSIENT_REASONS): the counter carries that alert. A contract
    or configuration failure (SNAPSHOT_ERROR_REASONS) goes to one fixed
    issue per failure kind (ALERT_SNAPSHOT_FAILURE), which pages.
    """
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
        capture_exception(
            error,
            context=context,
            fingerprint=alert_fingerprint(ALERT_SNAPSHOT_FAILURE, reason),
        )
    elif reason in SNAPSHOT_TRANSIENT_REASONS:
        # A RevenueCat outage would send one Sentry event per webhook; the
        # counter above carries the alert instead.
        pass
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
    downgrade_expected: bool,
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
        # Routine: the app's refresh right after a purchase often reads later
        # but commits before the purchase webhook. Logged and counted (the
        # subscription_access.sync counter above), not sent to Sentry.
        logger.warning(
            "RevenueCat snapshot older than stored facts skipped: user_id=%s "
            "source=%s event_type=%s event_id=%s",
            user.id,
            source,
            event_type,
            event_id,
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
            fingerprint=alert_fingerprint(ALERT_STANDARD_MISSING),
        )
    if outcome.downgraded:
        increment_metric(
            "subscription_access.downgrade",
            value=max(outcome.rows_revoked, 1),
            # A refund is a CANCELLATION too: only this attribute tells it
            # from an unexpected loss of access on the same event type.
            attributes={
                **attributes,
                "expected": "true" if downgrade_expected else "false",
            },
        )
        if downgrade_expected:
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
                fingerprint=alert_fingerprint(ALERT_UNEXPECTED_DOWNGRADE),
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
