"""RevenueCat webhook subscription lifecycle event handlers."""

import logging
import os
import uuid
from typing import Any

from sqlalchemy import text

from src.api.base_dependencies import get_subscription_service
from src.api.routes.v1.webhook_lookup_parsing import (
    get_subscription_by_revenuecat_id,
    parse_platform,
    parse_timestamp,
)
from src.api.routes.v1.webhook_referral_funnel import (
    credit_referral_on_purchase,
    revoke_referral_on_refund,
)
from src.app.events.affiliate.affiliate_events import (
    SubscriptionLifecycleEvent,
)
from src.domain.ports.integration_event_publisher_port import require_event_publisher
from src.domain.utils.timezone_utils import utc_now
from src.infra.adapters.posthog_adapter import PostHogAdapter
from src.infra.database.models.subscription import Subscription

logger = logging.getLogger(__name__)

POSTHOG_LIFECYCLE_EVENTS = {
    "CANCELLATION": "subscription_cancelled",
    "EXPIRATION": "subscription_expired",
    "BILLING_ISSUE": "subscription_billing_issue",
    "REFUND": "subscription_refunded",
    "RENEWAL": "subscription_renewed",
    "PRODUCT_CHANGE": "subscription_product_changed",
}


def _get_subscription_service():
    """Resolve the RevenueCat service through the existing dependency boundary."""
    return get_subscription_service()


async def _publish_subscription_event(
    event_publisher: Any,
    event_type: str,
    payload: dict,
    event_id: str | None,
    user_id: str,
) -> None:
    """Publish a subscription lifecycle event to Cloudflare Queues."""
    event = SubscriptionLifecycleEvent(
        event_id=_subscription_event_id(event_id),
        environment=os.getenv("ENVIRONMENT", "development"),
        aggregate_id=user_id,
        data={"lifecycle_type": event_type, **payload},
    )
    await event_publisher.publish(event.to_payload())


def _subscription_event_id(event_id: str | None) -> str:
    """Map the provider event identifier to the UUID required on the wire."""
    if not event_id:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(event_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"revenuecat:{event_id}"))


async def _notify_affiliate(
    affiliate_handler: Any,
    event_type: str,
    payload: dict,
    event_id: str | None,
    event_publisher: Any = None,
    user_id: str | None = None,
) -> None:
    """Publish subscription event to Cloudflare Queue or send affiliate notification."""
    if user_id is not None:
        await _publish_subscription_event(
            require_event_publisher(event_publisher),
            event_type,
            payload,
            event_id,
            user_id,
        )
    elif affiliate_handler is not None:
        resolved_event_id = event_id or str(uuid.uuid4())
        affiliate_payload = {
            **payload,
            "event_type": event_type,
            "event_id": resolved_event_id,
        }
        try:
            await affiliate_handler.send_event(affiliate_payload)
        except Exception as exc:
            logger.warning("Failed to send affiliate event: %s", exc)


async def handle_purchase(
    uow, user, event, affiliate_handler=None, event_publisher=None
):
    """Handle initial purchase."""
    logger.info(f"Creating subscription for user {user.id}")

    # Check if subscription already exists
    existing = await get_subscription_by_revenuecat_id(uow, event.get("app_user_id"))

    if existing:
        logger.warning(f"Subscription already exists for {user.id}, updating instead")
        await handle_renewal(uow, user, event, affiliate_handler, event_publisher)
        return

    # Create new subscription record
    subscription = Subscription(
        id=str(uuid.uuid4()),
        user_id=user.id,
        revenuecat_subscriber_id=event.get("app_user_id"),
        product_id=event.get("product_id"),
        platform=parse_platform(event.get("store")),
        status="active",
        purchased_at=parse_timestamp(event.get("purchased_at_ms")) or utc_now(),
        expires_at=parse_timestamp(event.get("expiration_at_ms")),
        store_transaction_id=event.get("transaction_id"),
        is_sandbox=event.get("environment") == "SANDBOX",
    )

    uow.session.add(subscription)
    logger.info(f"User {user.id} purchased {subscription.product_id}")

    # Credit referrer if this user has a pending referral conversion
    await credit_referral_on_purchase(uow, str(user.id))
    await _notify_affiliate(
        affiliate_handler,
        "subscription_initial_purchase",
        {
            "mealtrack_user_id": str(user.id),
            "product_id": event.get("product_id"),
            "period_type": event.get("period_type"),
            "subscription_id": event.get("transaction_id")
            or event.get("original_transaction_id"),
            "occurred_at": (
                parse_timestamp(event.get("purchased_at_ms")) or utc_now()
            ).isoformat(),
        },
        event.get("id"),
        event_publisher=event_publisher,
        user_id=str(user.id),
    )


async def handle_renewal(
    uow, user, event, affiliate_handler=None, event_publisher=None
):
    """Handle subscription renewal."""
    subscription = await get_subscription_by_revenuecat_id(
        uow, event.get("app_user_id")
    )

    if subscription:
        subscription.expires_at = parse_timestamp(event.get("expiration_at_ms"))
        subscription.status = "active"
        subscription.updated_at = utc_now()
        logger.info(
            f"User {user.id} renewed subscription until {subscription.expires_at}"
        )
    else:
        logger.warning("Subscription not found for renewal, creating new one")
        await handle_purchase(uow, user, event, affiliate_handler, event_publisher)
        return

    await capture_subscription_lifecycle_event(user, event, "RENEWAL", subscription)
    await _notify_affiliate(
        affiliate_handler,
        "subscription_renewal",
        {
            "mealtrack_user_id": str(user.id),
            "product_id": event.get("product_id"),
            "period_type": event.get("period_type"),
            "subscription_id": event.get("transaction_id")
            or event.get("original_transaction_id"),
            "occurred_at": utc_now().isoformat(),
        },
        event.get("id"),
        event_publisher=event_publisher,
        user_id=str(user.id),
    )

    # Purge stale trial-expiry pushes so we don't send a
    # "your trial ends tomorrow" push for a sub that just auto-renewed.
    try:
        await uow.session.execute(
            text("""
                DELETE FROM notifications
                WHERE user_id = :uid
                  AND notification_type LIKE 'trial_expiry%'
                  AND status = 'pending'
                """),
            {"uid": user.id},
        )
    except Exception:
        # Do not raise — webhook MUST still ACK so RevenueCat doesn't retry.
        logger.exception(
            "Failed to purge stale trial pushes for user %s after renewal", user.id
        )


async def handle_cancellation(
    uow, user, event, affiliate_handler=None, event_publisher=None
):
    """Handle subscription cancellation."""
    subscription = await get_or_create_subscription(uow, user, event)

    if subscription:
        subscription.status = "cancelled"
        subscription.cancelled_at = utc_now()
        subscription.updated_at = utc_now()
        # Note: User still has access until expires_at
        logger.info(
            f"User {user.id} cancelled subscription (expires {subscription.expires_at})"
        )

    await capture_subscription_lifecycle_event(
        user, event, "CANCELLATION", subscription
    )
    await _notify_affiliate(
        affiliate_handler,
        "subscription_canceled",
        {
            "mealtrack_user_id": str(user.id),
            "product_id": event.get("product_id"),
            "subscription_id": event.get("transaction_id")
            or event.get("original_transaction_id"),
            "occurred_at": utc_now().isoformat(),
        },
        event.get("id"),
        event_publisher=event_publisher,
        user_id=str(user.id),
    )


async def handle_expiration(
    uow, user, event, affiliate_handler=None, event_publisher=None
):
    """Handle subscription expiration."""
    subscription = await get_or_create_subscription(uow, user, event)

    if subscription:
        subscription.status = "expired"
        subscription.updated_at = utc_now()
        logger.info(f"User {user.id} subscription expired")

    await capture_subscription_lifecycle_event(user, event, "EXPIRATION", subscription)
    await _notify_affiliate(
        affiliate_handler,
        "subscription_expired",
        {
            "mealtrack_user_id": str(user.id),
            "product_id": event.get("product_id"),
            "subscription_id": event.get("transaction_id")
            or event.get("original_transaction_id"),
            "occurred_at": utc_now().isoformat(),
        },
        event.get("id"),
        event_publisher=event_publisher,
        user_id=str(user.id),
    )


async def handle_billing_issue(
    uow, user, event, affiliate_handler=None, event_publisher=None
):
    """Handle billing issues."""
    subscription = await get_or_create_subscription(uow, user, event)

    if subscription:
        subscription.status = "billing_issue"
        subscription.updated_at = utc_now()
        logger.warning(f"Billing issue for user {user.id}")

    await capture_subscription_lifecycle_event(
        user, event, "BILLING_ISSUE", subscription
    )
    await _notify_affiliate(
        affiliate_handler,
        "subscription_billing_issue",
        {
            "mealtrack_user_id": str(user.id),
            "product_id": event.get("product_id")
            or getattr(subscription, "product_id", None),
            "subscription_id": event.get("transaction_id")
            or event.get("original_transaction_id"),
            "occurred_at": utc_now().isoformat(),
        },
        event.get("id"),
        event_publisher=event_publisher,
        user_id=str(user.id),
    )


async def handle_product_change(
    uow, user, event, affiliate_handler=None, event_publisher=None
):
    """Handle product change (e.g., monthly to yearly)."""
    subscription = await get_or_create_subscription(uow, user, event)

    if subscription:
        subscription.product_id = event.get("product_id")
        subscription.expires_at = parse_timestamp(event.get("expiration_at_ms"))
        subscription.status = "active"
        subscription.updated_at = utc_now()
        logger.info(f"User {user.id} changed to {subscription.product_id}")

    await capture_subscription_lifecycle_event(
        user, event, "PRODUCT_CHANGE", subscription
    )
    await _notify_affiliate(
        affiliate_handler,
        "subscription_product_changed",
        {
            "mealtrack_user_id": str(user.id),
            "product_id": event.get("product_id"),
            "subscription_id": event.get("transaction_id")
            or event.get("original_transaction_id"),
            "occurred_at": utc_now().isoformat(),
        },
        event.get("id"),
        event_publisher=event_publisher,
        user_id=str(user.id),
    )


async def handle_refund(uow, user, event, affiliate_handler=None, event_publisher=None):
    """Handle refund — update subscription status and revoke referral credit."""
    subscription = await get_or_create_subscription(uow, user, event)
    if subscription:
        subscription.status = "refunded"
        subscription.updated_at = utc_now()
        logger.info(f"User {user.id} subscription refunded")

    await capture_subscription_lifecycle_event(user, event, "REFUND", subscription)
    await _notify_affiliate(
        affiliate_handler,
        "subscription_refund",
        {
            "mealtrack_user_id": str(user.id),
            "product_id": event.get("product_id"),
            "subscription_id": event.get("transaction_id")
            or event.get("original_transaction_id"),
            "occurred_at": utc_now().isoformat(),
        },
        event.get("id"),
        event_publisher=event_publisher,
        user_id=str(user.id),
    )
    await revoke_referral_on_refund(uow, str(user.id))


async def capture_subscription_lifecycle_event(
    user, event, event_type, subscription
) -> None:
    """Mirror RevenueCat lifecycle webhooks into PostHog when configured."""
    posthog_event = POSTHOG_LIFECYCLE_EVENTS.get(event_type)
    if not posthog_event:
        return

    properties = {
        "revenuecat_event_type": event_type,
        "product_id": event.get("product_id")
        or getattr(subscription, "product_id", None),
        "platform": parse_platform(event.get("store")),
        "store": event.get("store"),
        "environment": event.get("environment"),
        "subscription_status": getattr(subscription, "status", None),
        "expiration_at_ms": event.get("expiration_at_ms"),
        "purchased_at_ms": event.get("purchased_at_ms"),
        "cancel_reason": event.get("cancel_reason"),
        "period_type": event.get("period_type"),
        "is_sandbox": event.get("environment") == "SANDBOX",
    }
    await PostHogAdapter().capture(
        distinct_id=getattr(user, "firebase_uid", None) or str(user.id),
        event=posthog_event,
        properties={
            key: value for key, value in properties.items() if value is not None
        },
    )


async def get_or_create_subscription(uow, user, event):
    """Get existing subscription or create one if missing (handles missed INITIAL_PURCHASE)."""
    subscription = await uow.subscriptions.find_by_revenuecat_id(
        event.get("app_user_id")
    )

    if not subscription:
        logger.warning(
            f"No subscription found for user {user.id}, creating record (missed INITIAL_PURCHASE)"
        )
        subscription = Subscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            revenuecat_subscriber_id=event.get("app_user_id"),
            product_id=event.get("product_id") or "unknown",
            platform=parse_platform(event.get("store")),
            status="active",
            purchased_at=parse_timestamp(event.get("purchased_at_ms")) or utc_now(),
            expires_at=parse_timestamp(event.get("expiration_at_ms")),
            store_transaction_id=event.get("transaction_id"),
            is_sandbox=event.get("environment") == "SANDBOX",
        )
        uow.session.add(subscription)
        await uow.session.flush()

    return subscription
