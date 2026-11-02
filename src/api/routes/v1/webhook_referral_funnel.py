"""RevenueCat webhook referral wallet and transfer/redemption helpers."""

import logging

from src.api.base_dependencies import (
    get_subscription_service as _resolve_subscription_service,
)
from src.api.base_dependencies import (
    get_web_funnel_redemption_service,
)
from src.api.routes.v1.webhook_lookup_parsing import (
    preferred_transfer_target,
    store_platform,
)
from src.api.routes.v1.webhook_subscription_rows import (
    apply_subscription_info,
    entitled_lifetime_row,
    is_sandbox_event,
    load_subscriber_rows,
    lock_subscriber,
)
from src.domain.utils.timezone_utils import utc_now

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
    """Handle RevenueCat subscriber transfers without failing on anonymous IDs.

    RevenueCat moves the source subscribers' purchases from the event's store,
    so every row of that store's platform moves (every row when the store
    names no certain platform): a row left behind would keep Premium on the
    old account, and a web-funnel plan is not part of an App Store transfer.
    """
    transferred_from = event.get("transferred_from") or []
    transferred_to = event.get("transferred_to") or []
    if not transferred_from or not transferred_to:
        logger.info("RevenueCat transfer ignored: missing transfer IDs")
        return

    target_id = preferred_transfer_target(transferred_to)
    if not target_id:
        logger.info("RevenueCat transfer ignored: no valid transferred_to ID")
        return

    # One lock order for every transfer, so two of them can't deadlock.
    ids = {i for i in (*transferred_from, target_id) if isinstance(i, str) and i}
    for subscriber_id in sorted(ids):
        await lock_subscriber(uow, subscriber_id)
    rows = []
    for source_id in transferred_from:
        rows.extend(await load_subscriber_rows(uow, source_id))
    platform = store_platform(event.get("store"))
    if platform is not None:
        rows = [row for row in rows if row.platform == platform]

    if not rows:
        logger.info(
            "RevenueCat transfer ignored: no local subscription matched transferred_from=%s",
            transferred_from,
        )
        return

    for subscription in rows:
        subscription.revenuecat_subscriber_id = target_id
        subscription.updated_at = utc_now()
        logger.info(
            "RevenueCat transfer updated subscription %s to subscriber_id=%s",
            subscription.id,
            target_id,
        )
    # Sessions don't autoflush: the target's sync in this unit of work looks
    # rows up by the new subscriber id.
    await uow.session.flush()


async def sync_redeemed_target_subscription(uow, user, event: dict) -> None:
    """Refresh the target Firebase user's rows after a RevenueCat redemption.

    Every row the target's subscriber id holds now belongs to the target user,
    including a lifetime row RevenueCat's subscription info never lists.
    """
    current = await _get_subscription_service().get_subscription_info(user.firebase_uid)
    await _lock_subscription_cache(uow, user.firebase_uid)
    rows = await load_subscriber_rows(uow, user.firebase_uid)
    for row in rows:
        if row.user_id != user.id:
            row.user_id = user.id
            row.updated_at = utc_now()

    if not current:
        logger.info("RevenueCat redemption has no active target entitlement")
        return
    # RevenueCat reports only renewing products here: beside a lifetime row
    # that grants access, only the plan's own row is refreshed.
    held = entitled_lifetime_row(rows, sandbox=is_sandbox_event(event)) is not None
    apply_subscription_info(
        uow, user, event, user.firebase_uid, rows, current, lifetime_held=held
    )


async def _lock_subscription_cache(uow, firebase_uid: str) -> None:
    """Serialize cache creation for duplicate webhook deliveries of one user."""
    await lock_subscriber(uow, firebase_uid)


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
