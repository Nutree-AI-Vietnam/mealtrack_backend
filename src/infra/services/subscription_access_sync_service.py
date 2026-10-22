"""Normalize verified RevenueCat snapshots into database access facts."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.ports.subscription_service_port import (
    RevenueCatSnapshot,
    RevenueCatSnapshotUnavailable,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription
from src.infra.database.models.user.user import User


def _date(value: Any, *, field: str, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise RevenueCatSnapshotUnavailable(f"RevenueCat {field} is malformed")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RevenueCatSnapshotUnavailable(
            f"RevenueCat {field} is malformed"
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
    raise RevenueCatSnapshotUnavailable("RevenueCat store is unsupported")


def _advance_updated_at(user: User) -> None:
    previous = _utc(user.updated_at)
    setattr(  # noqa: B010 - SQLAlchemy model attributes are typed as Column descriptors
        user, "updated_at", max(utc_now(), previous + timedelta(microseconds=1))
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
            "RevenueCat standard entitlement is malformed"
        )

    product_id = standard.get("product_identifier")
    if not isinstance(product_id, str) or not product_id:
        raise RevenueCatSnapshotUnavailable("RevenueCat product is missing")
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
        raise RevenueCatSnapshotUnavailable("RevenueCat purchase metadata is missing")

    if "expires_date" not in standard:
        raise RevenueCatSnapshotUnavailable("RevenueCat expiry is missing")
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
        raise RevenueCatSnapshotUnavailable("RevenueCat environment is missing")

    if subscription.get("billing_issues_detected_at"):
        status = "billing_issue"
    elif expires_at is not None and expires_at <= now:
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
        "store_transaction_id": subscription.get("transaction_id")
        or subscription.get("original_transaction_id"),
    }


def _utc(value: Any) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def sync_revenuecat_access(
    session: AsyncSession,
    user: User,
    *,
    adapter: Any | None = None,
    refund_event: bool = False,
    snapshot: RevenueCatSnapshot | None = None,
    expected_updated_at: datetime | None = None,
) -> None:
    """Apply one current provider snapshot, preserving state on ambiguity/errors."""
    expected_updated_at = _utc(expected_updated_at or user.updated_at)  # type: ignore[arg-type]
    if snapshot is None:
        if adapter is None:
            raise RevenueCatSnapshotUnavailable("RevenueCat adapter is required")
        snapshot = await adapter.get_verified_snapshot(user.firebase_uid)
    locked_user = await session.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_user is None:
        raise RevenueCatSnapshotUnavailable("Subscription owner disappeared")
    if _utc(locked_user.updated_at) != expected_updated_at:  # type: ignore[arg-type]
        raise RevenueCatSnapshotUnavailable("Subscription owner changed during sync")
    existing = list(
        (
            await session.scalars(
                select(Subscription)
                .where(
                    Subscription.user_id == user.id,
                    Subscription.provider == "revenuecat",
                )
                .with_for_update()
            )
        ).all()
    )

    if snapshot.absent:
        if existing:
            raise RevenueCatSnapshotUnavailable(
                "RevenueCat absence cannot verify existing subscription history"
            )
        _advance_updated_at(locked_user)
        return

    if snapshot.subscriber is None or snapshot.request_date is None:
        raise RevenueCatSnapshotUnavailable("RevenueCat snapshot is incomplete")
    facts = normalize_standard_access_facts(snapshot.subscriber)
    if facts and facts["status"] == "expired":
        facts = None
    latest_snapshot = max(
        (
            _utc(row.provider_snapshot_at)  # type: ignore[arg-type]
            for row in existing
            if row.provider_snapshot_at
        ),
        default=datetime.min.replace(tzinfo=UTC),
    )
    if snapshot.request_date < latest_snapshot:
        return
    if snapshot.request_date == latest_snapshot:
        matching = [
            row
            for row in existing
            if _utc(row.provider_snapshot_at) == latest_snapshot  # type: ignore[arg-type]
        ]
        if facts and len(matching) == 1:
            row = matching[0]
            if all(getattr(row, key) == value for key, value in facts.items()):
                return
        elif not facts and all(
            row.status == ("refunded" if refund_event else "expired")
            for row in matching
        ):
            return
        raise RevenueCatSnapshotUnavailable(
            "RevenueCat returned conflicting facts at the same snapshot time"
        )

    for row in existing:
        setattr(row, "status", "refunded" if refund_event else "expired")  # noqa: B010
        setattr(row, "grace_period_expires_at", None)  # noqa: B010
        setattr(row, "provider_snapshot_at", snapshot.request_date)  # noqa: B010

    if facts and facts["status"] != "expired":
        target_row = next(
            (item for item in existing if item.product_id == facts["product_id"]), None
        )
        if target_row is None:
            target_row = Subscription(
                id=str(uuid4()),
                user_id=user.id,
                revenuecat_subscriber_id=user.firebase_uid,
                product_id=facts["product_id"],
                platform=facts["platform"],
                purchased_at=facts["purchased_at"],
                status=facts["status"],
                is_sandbox=facts["is_sandbox"],
            )
            session.add(target_row)
        for key, value in facts.items():
            setattr(target_row, key, value)
        setattr(target_row, "provider", "revenuecat")  # noqa: B010
        setattr(target_row, "revenuecat_subscriber_id", user.firebase_uid)  # noqa: B010
        setattr(target_row, "provider_snapshot_at", snapshot.request_date)  # noqa: B010
    _advance_updated_at(locked_user)
