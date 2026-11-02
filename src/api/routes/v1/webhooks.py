"""
Webhook handlers for RevenueCat events.

Thin router — business logic lives in sibling modules.
"""

import hmac
import logging
import os
import uuid

from fastapi import APIRouter, Header, HTTPException, Request

from src.api.base_dependencies import get_cache_service, get_streak_restore_policy
from src.api.routes.v1.webhook_lifetime_purchase import (
    fetch_revenuecat_plan,
    handle_lifetime_purchase,
    handle_lifetime_refund,
)
from src.api.routes.v1.webhook_lookup_parsing import (
    candidate_revenuecat_ids,
    find_user_for_revenuecat_event,
    is_anonymous_event,
    parse_platform,
    parse_timestamp,
)
from src.api.routes.v1.webhook_play_renewal_cancel import (
    PlayRenewal,
    cancel_play_renewals,
    play_plan_beside_lifetime,
)
from src.api.routes.v1.webhook_referral_funnel import (
    _get_subscription_service,
    credit_referral_on_purchase,
    handle_transfer,
    record_web_funnel_redemption,
    revoke_referral_on_refund,
    sync_redeemed_target_subscription,
)
from src.api.routes.v1.webhook_streak_restore import handle_streak_restore_event
from src.api.routes.v1.webhook_subscription_lifecycle import (
    handle_billing_issue,
    handle_cancellation,
    handle_expiration,
    handle_product_change,
    handle_purchase,
    handle_refund,
    handle_renewal,
)
from src.app.services.streak_restore_state import purge_streak_cache
from src.app.services.web_funnel_claim_payment import reconcile_revenuecat_event
from src.bootstrap.integration_services import (
    get_affiliate_service,
    get_integration_event_publisher,
)
from src.bootstrap.web_funnel_claim_dispatcher import get_web_funnel_outbox_dispatcher
from src.domain.constants.lifetime_products import is_lifetime_product
from src.infra.database.uow_async import AsyncUnitOfWork
from src.observability import increment_metric

router = APIRouter(prefix="/v1/webhooks", tags=["Webhooks"])
logger = logging.getLogger(__name__)

# RevenueCat reports a one-time refund as CANCELLATION; REFUND is accepted too.
LIFETIME_REFUND_EVENTS = {"CANCELLATION", "REFUND"}

NON_RETRYABLE_USERLESS_EVENTS = {
    "TRANSFER",
    "CANCELLATION",
    "EXPIRATION",
    "BILLING_ISSUE",
    "PRODUCT_CHANGE",
    "REFUND",
}

# Backward-compatible aliases for tests and external imports
_candidate_revenuecat_ids = candidate_revenuecat_ids
_is_anonymous_event = is_anonymous_event
_record_web_funnel_redemption = record_web_funnel_redemption
_credit_referral_on_purchase = credit_referral_on_purchase
_revoke_referral_on_refund = revoke_referral_on_refund

__all__ = [
    "router",
    "revenuecat_webhook",
    "webhook_health",
    "find_user_for_revenuecat_event",
    "handle_purchase",
    "handle_lifetime_purchase",
    "handle_lifetime_refund",
    "handle_renewal",
    "handle_cancellation",
    "handle_expiration",
    "handle_billing_issue",
    "handle_product_change",
    "handle_refund",
    "handle_transfer",
    "parse_platform",
    "parse_timestamp",
]


def _get_event_publisher():
    """Return the required Cloudflare Queue publisher."""
    return get_integration_event_publisher()


def _get_affiliate_handler():
    """Return AffiliateServiceAdapter if affiliate integration is enabled, else None."""
    if os.getenv("AFFILIATE_INTEGRATION_ENABLED", "").lower() not in ("1", "true"):
        return None
    try:
        return get_affiliate_service(enabled=True)
    except Exception:
        logger.warning(
            "AffiliateServiceAdapter unavailable — affiliate events will not be dispatched"
        )
        return None


@router.post("/revenuecat")
async def revenuecat_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    """
    Handle RevenueCat webhook events.
    Verifies auth token against REVENUECAT_WEBHOOK_AUTH_HEADER if configured.
    """
    auth_header = os.getenv("REVENUECAT_WEBHOOK_AUTH_HEADER")
    if auth_header:
        if not authorization or not hmac.compare_digest(
            authorization.strip(), auth_header.strip()
        ):
            raise HTTPException(status_code=401, detail="Invalid authorization header")

    # Verify webhook is configured - reject if secret not set
    webhook_secret = os.getenv("REVENUECAT_WEBHOOK_SECRET", "")
    if not webhook_secret and not auth_header:
        logger.error("RevenueCat webhook not configured - rejecting request")
        raise HTTPException(status_code=503, detail="Webhook not configured")

    # Verify authorization (constant-time comparison to prevent timing attacks)
    if not auth_header and not hmac.compare_digest(authorization or "", webhook_secret):
        logger.warning("Invalid RevenueCat webhook authorization")
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Parse webhook payload
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON") from e

    # Extract event data
    event = payload.get("event", {})
    event_type = event.get("type")
    app_user_id = event.get("app_user_id")

    if not event_type:
        raise HTTPException(status_code=400, detail="Missing event type")

    async with AsyncUnitOfWork() as redemption_uow:
        if await record_web_funnel_redemption(redemption_uow, event):
            return {"status": "success"}

    # A paid web lead is deliberately recognized only by an exact backend UUID.
    # It is reconciled from fetched provider state before the native path, which
    # continues untouched for every non-lead event.
    if isinstance(app_user_id, str):
        try:
            lead_id = str(uuid.UUID(app_user_id))
        except ValueError:
            pass
        else:
            async with AsyncUnitOfWork() as web_funnel_uow:
                subscriber = await _get_subscription_service().get_subscriber_info(
                    lead_id
                )
                if await reconcile_revenuecat_event(
                    web_funnel_uow.session, event, subscriber
                ):
                    await get_web_funnel_outbox_dispatcher()(lead_id=lead_id)
                    return {"status": "success"}

    logger.info(
        "RevenueCat webhook received: event_type=%s environment=%s",
        event_type,
        event.get("environment"),
    )

    # Resolve shared dependencies for affiliate delivery
    affiliate_handler = _get_affiliate_handler()
    event_publisher = _get_event_publisher()

    # Asked before the unit of work opens: no pooled connection or subscriber
    # lock waits on RevenueCat.
    lifetime_refund = event_type in LIFETIME_REFUND_EVENTS and is_lifetime_product(
        event.get("product_id")
    )
    refund_plan = await fetch_revenuecat_plan(event) if lifetime_refund else None
    play_renewals: list[PlayRenewal] = []

    # Get user
    async with AsyncUnitOfWork() as uow:
        user = await find_user_for_revenuecat_event(uow, event)

        if event_type == "TRANSFER":
            if not user:
                logger.info("RevenueCat transfer ignored: target user not found")
                return {"status": "ignored", "reason": "user_not_found"}
            await handle_transfer(uow, event)
            await sync_redeemed_target_subscription(uow, user, event)
            increment_metric(
                "webhook.revenuecat.processed",
                attributes={"event_type": event_type, "status": "success"},
            )
            return {"status": "success"}

        if event_type == "PURCHASE_REDEEMED":
            if not user:
                logger.info("RevenueCat purchase redemption target user not found")
                raise HTTPException(status_code=404, detail="User not found")
            await sync_redeemed_target_subscription(uow, user, event)
            increment_metric(
                "webhook.revenuecat.processed",
                attributes={"event_type": event_type, "status": "success"},
            )
            return {"status": "success"}

        if not user:
            logger.error(
                "RevenueCat webhook: user not found — event_type=%s "
                "environment=%s id_candidates=%s",
                event_type,
                event.get("environment"),
                len(candidate_revenuecat_ids(event)),
            )
            if event_type in NON_RETRYABLE_USERLESS_EVENTS or is_anonymous_event(event):
                return {"status": "ignored", "reason": "user_not_found"}
            raise HTTPException(status_code=404, detail="User not found")

        pack = await handle_streak_restore_event(
            uow, user, event, get_streak_restore_policy()
        )

        # Handle events — commit/rollback is owned by the AsyncUnitOfWork context manager
        if pack is not None:
            pass  # a streak restore pack never reaches the subscription handlers
        elif event_type == "INITIAL_PURCHASE":
            await handle_purchase(uow, user, event, affiliate_handler, event_publisher)
            play_renewals = await play_plan_beside_lifetime(uow, user, event)

        elif event_type == "NON_RENEWING_PURCHASE" and is_lifetime_product(
            event.get("product_id")
        ):
            play_renewals = await handle_lifetime_purchase(
                uow, user, event, affiliate_handler, event_publisher
            )

        elif lifetime_refund:
            await handle_lifetime_refund(
                uow, user, event, affiliate_handler, event_publisher, refund_plan
            )

        elif event_type == "RENEWAL":
            await handle_renewal(uow, user, event, affiliate_handler, event_publisher)
            play_renewals = await play_plan_beside_lifetime(uow, user, event)

        elif event_type == "CANCELLATION":
            await handle_cancellation(
                uow, user, event, affiliate_handler, event_publisher
            )

        elif event_type == "EXPIRATION":
            await handle_expiration(
                uow, user, event, affiliate_handler, event_publisher
            )

        elif event_type == "BILLING_ISSUE":
            await handle_billing_issue(
                uow, user, event, affiliate_handler, event_publisher
            )

        elif event_type == "PRODUCT_CHANGE":
            await handle_product_change(
                uow, user, event, affiliate_handler, event_publisher
            )

        elif event_type == "REFUND":
            await handle_refund(uow, user, event, affiliate_handler, event_publisher)

    # After the commit, so a read that refills the cache sees the new balance.
    if pack is not None and pack.owner_id:
        await purge_streak_cache(get_cache_service(), pack.owner_id)

    # After the commit too: the event is stored whatever Google does.
    await cancel_play_renewals(event, play_renewals)

    increment_metric(
        "webhook.revenuecat.processed",
        attributes={"event_type": event_type or "unknown", "status": "success"},
    )
    return {"status": "success"}


@router.get("/revenuecat/health")
async def webhook_health():
    """Health check for webhook."""
    return {"status": "ok", "service": "revenuecat_webhook"}
