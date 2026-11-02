"""RevenueCat webhook subscription lifecycle event handlers."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text

from src.api.base_dependencies import get_subscription_service
from src.api.routes.v1.webhook_lifecycle_events import (
    notify_affiliate as _notify_affiliate,
)
from src.api.routes.v1.webhook_lifecycle_events import (
    publish_subscription_event as _publish_subscription_event,
)
from src.api.routes.v1.webhook_lookup_parsing import parse_timestamp
from src.api.routes.v1.webhook_referral_funnel import (
    credit_referral_on_purchase,
    revoke_referral_on_refund,
)
from src.api.routes.v1.webhook_subscription_rows import (
    find_renewing_target,
    get_or_create_subscription,
    keeps_paid_access,
    lifetime_holds_access,
    new_subscription_row,
)
from src.bootstrap.engagement_push import engagement_push_enabled
from src.domain.model.notification import (
    EngagementPushKind,
    plan_billing_issue_push,
)
from src.domain.utils.timezone_utils import utc_now

logger = logging.getLogger(__name__)

__all__ = ["_notify_affiliate", "_publish_subscription_event"]


def _get_subscription_service():
    """Resolve the RevenueCat service through the existing dependency boundary."""
    return get_subscription_service()


async def handle_purchase(
    uow, user, event, affiliate_handler=None, event_publisher=None
):
    """Handle initial purchase."""
    logger.info(f"Creating subscription for user {user.id}")
    target = await find_renewing_target(uow, user, event)  # takes the subscriber lock

    if target.row is not None:
        logger.warning(f"Subscription already exists for {user.id}, updating instead")
        await handle_renewal(uow, user, event, affiliate_handler, event_publisher)
        return

    # Beside a lifetime row (the plan an upgrade replaced, even when the
    # upgrade's webhook came first) no row is written, but it is still a purchase.
    if not target.kept_for_lifetime:
        subscription = new_subscription_row(
            user,
            event,
            product_id=event.get("product_id"),
            expires_at=parse_timestamp(event.get("expiration_at_ms")),
        )
        uow.session.add(subscription)
        logger.info(f"User {user.id} purchased {subscription.product_id}")

    # Credit referrer if this user has a pending referral conversion
    await credit_referral_on_purchase(uow, str(user.id))
    if target.kept_for_lifetime and target.had_paid_purchase:
        logger.info("Purchase beside lifetime not reported again: user_id=%s", user.id)
    else:
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
    await _drop_billing_issue_push(uow, user)


async def handle_renewal(
    uow, user, event, affiliate_handler=None, event_publisher=None
):
    """Handle subscription renewal."""
    target = await find_renewing_target(uow, user, event)
    subscription = target.row

    if subscription:
        subscription.expires_at = parse_timestamp(event.get("expiration_at_ms"))
        subscription.status = "active"
        subscription.updated_at = utc_now()
        logger.info(
            f"User {user.id} renewed subscription until {subscription.expires_at}"
        )
    elif not target.kept_for_lifetime:
        # A missed INITIAL_PURCHASE: store the plan, but report what this is.
        logger.warning("Subscription not found for renewal, creating new one")
        subscription = new_subscription_row(
            user,
            event,
            product_id=event.get("product_id"),
            expires_at=parse_timestamp(event.get("expiration_at_ms")),
        )
        uow.session.add(subscription)
        await credit_referral_on_purchase(uow, str(user.id))

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

    await _drop_billing_issue_push(uow, user)


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
    # A repeat BILLING_ISSUE in the same episode must not push again.
    already_flagged = getattr(subscription, "status", None) == "billing_issue"

    if subscription:
        subscription.status = "billing_issue"
        subscription.updated_at = utc_now()
        logger.warning(f"Billing issue for user {user.id}")

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

    # While a lifetime row grants access a failed plan charge costs nothing.
    if (
        subscription is not None
        and not already_flagged
        and not await lifetime_holds_access(uow, event)
    ):
        await _queue_billing_issue_push(uow, user)


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
    await _drop_billing_issue_push(uow, user)


async def handle_refund(uow, user, event, affiliate_handler=None, event_publisher=None):
    """Handle refund — update subscription status and revoke referral credit."""
    subscription = await get_or_create_subscription(uow, user, event)
    if subscription:
        subscription.status = "refunded"
        subscription.updated_at = utc_now()
        logger.info(f"User {user.id} subscription refunded")

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
    # The referral stands while the buyer keeps paid access: a lifetime row
    # (no row returned above) or another paid plan.
    if subscription is not None and not await keeps_paid_access(
        uow, event, subscription
    ):
        await revoke_referral_on_refund(uow, str(user.id))


async def _queue_billing_issue_push(uow, user) -> None:
    """Queue the payment-failed push for the user's next 09:00–20:00 window."""
    if not engagement_push_enabled():
        return
    push = plan_billing_issue_push(
        user_id=str(user.id),
        timezone=_text_attr(user, "timezone"),
        language=_text_attr(user, "language_code"),
        now=utc_now(),
    )

    async def enqueue() -> None:
        if await uow.engagement_pushes.enqueue(push):
            logger.info(
                "Billing-issue push for user %s due at %s",
                user.id,
                push.scheduled_for_utc.isoformat(),
            )

    await _write_push_outbox(uow, user, "queue", enqueue)


async def _drop_billing_issue_push(uow, user) -> None:
    """The billing issue is resolved, so its pending push must not go out."""

    async def delete() -> None:
        await uow.engagement_pushes.delete_pending(
            user_id=str(user.id), kind=EngagementPushKind.BILLING_ISSUE
        )

    await _write_push_outbox(uow, user, "drop", delete)


async def _write_push_outbox(
    uow, user, action: str, write: Callable[[], Awaitable[None]]
) -> None:
    # The savepoint keeps a failed outbox write from aborting the subscription
    # update: the webhook must still ACK so RevenueCat doesn't retry.
    try:
        async with uow.session.begin_nested():
            await write()
    except Exception:
        logger.exception(
            "Failed to %s the billing-issue push for user %s", action, user.id
        )


def _text_attr(obj: Any, name: str) -> str | None:
    value = getattr(obj, name, None)
    return value if isinstance(value, str) and value else None
