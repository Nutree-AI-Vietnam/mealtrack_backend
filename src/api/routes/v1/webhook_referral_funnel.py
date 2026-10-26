"""RevenueCat webhook referral wallet and web redemption helpers."""

import logging

from src.api.base_dependencies import (
    get_subscription_service as _resolve_subscription_service,
)
from src.api.base_dependencies import (
    get_web_funnel_redemption_service,
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
