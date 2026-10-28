"""
Webhook handlers for RevenueCat events.

Thin router — business logic lives in sibling modules.
"""

import hmac
import logging
import os
import uuid
from typing import cast

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.base_dependencies import lock_subscription_owner
from src.api.routes.v1.webhook_access_sync import (
    ACCESS_ACCOUNT_DELETED,
    ACCESS_NOT_SYNCED,
    ACCESS_SYNCED,
    ACCESS_UNRESOLVED,
    AccessRead,
    account_is_deleted,
    apply_access,
    capture_facts_for,
    event_syncs_access,
    is_refund_event,
    read_access,
)
from src.api.routes.v1.webhook_lookup_parsing import (
    candidate_revenuecat_ids,
    find_transfer_parties,
    find_user_for_revenuecat_event,
    is_anonymous_event,
    parse_platform,
    parse_timestamp,
)
from src.api.routes.v1.webhook_referral_funnel import (
    _get_subscription_service,
    credit_referral_on_purchase,
    record_web_funnel_redemption,
    revoke_referral_on_refund,
)
from src.api.routes.v1.webhook_subscription_lifecycle import (
    handle_billing_issue,
    handle_cancellation,
    handle_expiration,
    handle_product_change,
    handle_purchase,
    handle_refund,
    handle_renewal,
)
from src.app.services.web_funnel_claim_payment import reconcile_revenuecat_event
from src.bootstrap.integration_services import (
    get_affiliate_service,
    get_integration_event_publisher,
)
from src.bootstrap.web_funnel_claim_dispatcher import get_web_funnel_outbox_dispatcher
from src.infra.database.uow_async import AsyncUnitOfWork
from src.observability import increment_metric

router = APIRouter(prefix="/v1/webhooks", tags=["Webhooks"])
logger = logging.getLogger(__name__)

NON_RETRYABLE_USERLESS_EVENTS = {
    "TRANSFER",
    "CANCELLATION",
    "EXPIRATION",
    "BILLING_ISSUE",
    "PRODUCT_CHANGE",
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
    "handle_renewal",
    "handle_cancellation",
    "handle_expiration",
    "handle_billing_issue",
    "handle_product_change",
    "handle_refund",
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
                    cast(AsyncSession, web_funnel_uow.session), event, subscriber
                ):
                    await get_web_funnel_outbox_dispatcher()(lead_id=lead_id)
                    return {"status": "success"}

    logger.info(
        "RevenueCat webhook received: event_type=%s environment=%s",
        event_type,
        event.get("environment"),
    )

    if event_type == "TRANSFER":
        return await _process_transfer(event)

    # Resolve shared dependencies for affiliate delivery
    affiliate_handler = _get_affiliate_handler()
    event_publisher = _get_event_publisher()

    # The RevenueCat read happens before the transaction: no row lock is held
    # across the HTTP call.
    access_read: AccessRead | None = None
    if event_syncs_access(event_type):
        async with AsyncUnitOfWork() as lookup_uow:
            lookup_user = await find_user_for_revenuecat_event(lookup_uow, event)
        if lookup_user is not None and not account_is_deleted(lookup_user):
            access_read = await read_access(
                _get_subscription_service(),
                lookup_user,
                candidate_revenuecat_ids(event),
            )

    access_result = ACCESS_NOT_SYNCED
    async with AsyncUnitOfWork() as uow:
        session = cast(AsyncSession, uow.session)
        user = await find_user_for_revenuecat_event(uow, event)

        if not user:
            if event_type == "PURCHASE_REDEEMED":
                logger.info("RevenueCat purchase redemption target user not found")
                raise HTTPException(status_code=404, detail="User not found")
            logger.error(
                "RevenueCat webhook: user not found — event_type=%s "
                "environment=%s id_candidates=%s",
                event_type,
                event.get("environment"),
                len(candidate_revenuecat_ids(event)),
            )
            if (
                event_type in NON_RETRYABLE_USERLESS_EVENTS
                or not event_syncs_access(event_type)
                or is_anonymous_event(event)
            ):
                return {"status": "ignored", "reason": "user_not_found"}
            raise HTTPException(status_code=404, detail="User not found")

        # Serialise every delivery for this user on the user row, so the
        # legacy handlers and the access sync of concurrent webhooks queue
        # instead of interleaving.
        await lock_subscription_owner(session, user.id)
        facts_before_event = await capture_facts_for(session, user, access_read)

        # A refund arrives as a CANCELLATION. Its classification only labels
        # the access sync below (the access it removes is an expected
        # downgrade, revoked rows read 'refunded') and the logs; it moves no
        # money (see the CANCELLATION branch).
        refund_event = is_refund_event(event)
        # Legacy event effects -- commit/rollback is owned by the unit of work.
        if event_type == "INITIAL_PURCHASE":
            await handle_purchase(uow, user, event, affiliate_handler, event_publisher)

        elif event_type == "RENEWAL":
            await handle_renewal(uow, user, event, affiliate_handler, event_publisher)

        elif event_type == "CANCELLATION":
            # Refunds included: the cancellation path production has always
            # run, so a refund publishes 'subscription_canceled' and claws
            # back no referral commission. The refund clawback (handle_refund)
            # stays off until product decides how it should work
            # (docs/external-services.md, "Refunds and referral commission").
            if refund_event:
                logger.info(
                    "RevenueCat refund received; referral clawback is disabled: "
                    "user_id=%s event_id=%s cancel_reason=%s",
                    user.id,
                    event.get("id"),
                    event.get("cancel_reason"),
                )
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

        if event_syncs_access(event_type) and account_is_deleted(user):
            # The legacy effects above still run (referral credit, lifecycle
            # events); there is no access left to sync.
            logger.info(
                "RevenueCat event for a deleted account: access not synced — "
                "user_id=%s event_type=%s",
                user.id,
                event_type,
            )
            access_result = ACCESS_ACCOUNT_DELETED
        elif event_syncs_access(event_type):
            access_result = await apply_access(
                session,
                user,
                event,
                access_read,
                refund_event=refund_event,
                facts_before_event=facts_before_event,
            )

    increment_metric(
        "webhook.revenuecat.processed",
        attributes={
            "event_type": event_type or "unknown",
            "status": "success",
            "result": access_result,
        },
    )
    return {"status": "success"}


async def _process_transfer(event: dict) -> dict:
    """Re-read every local party of a TRANSFER from its own RevenueCat customer.

    RevenueCat moved purchases between app user ids. Nothing is moved or
    expired blindly: each resolvable source and target user is synced from
    its own verified customer, so a source keeps whatever RevenueCat still
    holds for it (another store's subscription, a web purchase) and loses
    only what RevenueCat moved. Only provider='revenuecat' rows are touched.
    A party whose customer cannot be read is left unresolved for repair; a
    source is also remembered as one, so the repair (a refresh, reconcile
    --repair, its next webhook) can apply its emptied customer.
    """
    transfer_ids = [
        identity
        for key in ("transferred_from", "transferred_to")
        for identity in event.get(key) or []
        if isinstance(identity, str) and identity
    ]
    async with AsyncUnitOfWork() as lookup_uow:
        found = await find_transfer_parties(lookup_uow, event)
    if not found:
        logger.info("RevenueCat transfer ignored: no local user for transfer ids")
        return {"status": "ignored", "reason": "user_not_found"}
    # A deleted account (often the source: the user deleted it, signed up
    # again and restored) has no access left to sync.
    parties = [party for party in found if not account_is_deleted(party.user)]
    if not parties:
        logger.info("RevenueCat transfer ignored: only deleted accounts involved")
        return {"status": "ignored", "reason": "account_deleted"}

    sources = {party.user_id for party in parties if party.is_source_only}
    subscription_service = _get_subscription_service()
    reads = {
        party.user_id: await read_access(
            subscription_service,
            party.user,
            transfer_ids,
            absence_is_authoritative=party.is_source_only,
        )
        for party in parties
    }
    results = []
    async with AsyncUnitOfWork() as uow:
        session = cast(AsyncSession, uow.session)
        # A fixed lock order keeps concurrent A->B and B->A transfers apart.
        for user_id in sorted(reads):
            user = await lock_subscription_owner(session, user_id)
            if user is None:
                continue
            # A source losing what RevenueCat moved away is the transfer
            # itself, not an unexpected downgrade.
            results.append(
                await apply_access(
                    session,
                    user,
                    event,
                    reads[user_id],
                    downgrade_expected=user_id in sources,
                    transfer_source=user_id in sources,
                )
            )
    logger.info(
        "RevenueCat transfer applied: parties=%d results=%s",
        len(parties),
        ",".join(results),
    )
    increment_metric(
        "webhook.revenuecat.processed",
        attributes={
            "event_type": "TRANSFER",
            "status": "success",
            "result": (
                ACCESS_UNRESOLVED if ACCESS_UNRESOLVED in results else ACCESS_SYNCED
            ),
        },
    )
    return {"status": "success"}


@router.get("/revenuecat/health")
async def webhook_health():
    """Health check for webhook."""
    return {"status": "ok", "service": "revenuecat_webhook"}
