"""RevenueCat webhook referral wallet and transfer/redemption helpers."""

import logging
import uuid

from sqlalchemy import text

from src.api.base_dependencies import (
    get_subscription_service as _resolve_subscription_service,
)
from src.api.base_dependencies import (
    get_web_funnel_redemption_service,
)
from src.api.routes.v1.webhook_lookup_parsing import (
    get_subscription_by_revenuecat_id,
    parse_platform,
    parse_revenuecat_expiry,
    parse_timestamp,
    preferred_transfer_target,
)
from src.domain.utils.timezone_utils import utc_now
from src.infra.database.models.subscription import Subscription

logger = logging.getLogger(__name__)


def _get_subscription_service():
    """Resolve the RevenueCat service through the existing dependency boundary."""
    return _resolve_subscription_service()


async def record_web_funnel_redemption(uow, event: dict) -> bool:
    """Store the provider-authenticated redeemer before any native user lookup."""
    return await get_web_funnel_redemption_service().record_webhook_redemption(
        uow.session, event
    )


async def handle_transfer(uow, event):
    """Handle RevenueCat subscriber transfers without failing on anonymous IDs."""
    transferred_from = event.get("transferred_from") or []
    transferred_to = event.get("transferred_to") or []
    if not transferred_from or not transferred_to:
        logger.info("RevenueCat transfer ignored: missing transfer IDs")
        return

    subscription = None
    for source_id in transferred_from:
        subscription = await uow.subscriptions.find_by_revenuecat_id(source_id)
        if subscription:
            break

    if not subscription:
        logger.info(
            "RevenueCat transfer ignored: no local subscription matched transferred_from=%s",
            transferred_from,
        )
        return

    target_id = preferred_transfer_target(transferred_to)
    if not target_id:
        logger.info("RevenueCat transfer ignored: no valid transferred_to ID")
        return

    subscription.revenuecat_subscriber_id = target_id
    subscription.updated_at = utc_now()
    logger.info(
        "RevenueCat transfer updated subscription %s to subscriber_id=%s",
        subscription.id,
        target_id,
    )


async def sync_redeemed_target_subscription(uow, user, event: dict) -> None:
    """Refresh the target Firebase user's cache after a RevenueCat redemption."""
    current = await _get_subscription_service().get_subscription_info(user.firebase_uid)
    if not current:
        logger.info("RevenueCat redemption has no active target entitlement")
        return

    await _lock_subscription_cache(uow, user.firebase_uid)
    subscription = await get_subscription_by_revenuecat_id(uow, user.firebase_uid)
    if subscription is None:
        subscription = Subscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            revenuecat_subscriber_id=user.firebase_uid,
            product_id=current.get("product_id")
            or event.get("product_id")
            or "unknown",
            platform=parse_platform(current.get("store") or event.get("store")),
            status="active",
            purchased_at=parse_timestamp(event.get("purchased_at_ms")) or utc_now(),
            expires_at=parse_revenuecat_expiry(current.get("expires_date")),
            store_transaction_id=event.get("transaction_id"),
            is_sandbox=event.get("environment") == "SANDBOX",
        )
        uow.session.add(subscription)
        return

    subscription.user_id = user.id
    subscription.product_id = current.get("product_id") or subscription.product_id
    subscription.platform = parse_platform(current.get("store") or event.get("store"))
    subscription.status = "active"
    subscription.expires_at = parse_revenuecat_expiry(current.get("expires_date"))
    subscription.updated_at = utc_now()


async def _lock_subscription_cache(uow, firebase_uid: str) -> None:
    """Serialize cache creation for duplicate webhook deliveries of one user."""
    await uow.session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:firebase_uid))"),
        {"firebase_uid": firebase_uid},
    )


async def credit_referral_on_purchase(uow, user_id: str) -> None:
    """Credit the referrer's wallet when a referred user completes their first purchase."""
    repo = uow.referrals
    conversion = await repo.get_conversion_by_referred_user(user_id, for_update=True)
    if conversion and conversion.status == "pending":
        conversion.status = "converted"
        conversion.converted_at = utc_now()
        # Use VND amount for wallet (fallback to commission_amount for old records)
        amount_vnd = conversion.commission_amount_vnd or conversion.commission_amount
        await repo.credit_wallet(conversion.referrer_user_id, amount_vnd)
        logger.info(
            "Referral credited: referrer=%s amount=%d VND (original: %d %s)",
            conversion.referrer_user_id,
            amount_vnd,
            conversion.commission_amount,
            conversion.commission_currency or "VND",
        )


async def revoke_referral_on_refund(uow, user_id: str) -> None:
    """Revoke the referrer's wallet credit when a referred user is refunded."""
    repo = uow.referrals
    conversion = await repo.get_conversion_by_referred_user(user_id, for_update=True)
    if conversion and conversion.status == "converted":
        conversion.status = "revoked"
        conversion.revoked_at = utc_now()
        # Use VND amount for wallet (fallback to commission_amount for old records)
        amount_vnd = conversion.commission_amount_vnd or conversion.commission_amount
        await repo.revoke_from_wallet(conversion.referrer_user_id, amount_vnd)
        logger.info(
            "Referral revoked: referrer=%s amount=%d VND",
            conversion.referrer_user_id,
            amount_vnd,
        )
