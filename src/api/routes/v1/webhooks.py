"""
Webhook handlers for RevenueCat events.

Thin router — business logic lives in sibling modules.
"""

import asyncio
import hmac
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from src.api.base_dependencies import (
    get_entitlement_inbox_service,
    get_feature_flag_reader,
)
from src.api.routes.v1.webhook_lookup_parsing import (
    candidate_revenuecat_ids,
    find_user_for_revenuecat_event,
    is_anonymous_event,
    load_user_by_id,
    parse_platform,
    parse_timestamp,
)
from src.api.routes.v1.webhook_referral_funnel import (
    _get_subscription_service,
    credit_referral_on_purchase,
    handle_transfer,
    record_web_funnel_redemption,
    revoke_referral_on_refund,
    sync_redeemed_target_subscription,
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
from src.domain.model.billing.constants import (
    FLAG_ENT_LEDGER_WRITE,
    FLAG_ENT_RECONCILE,
    ParkReason,
    SyncLane,
)
from src.infra.config.settings import settings
from src.infra.database.uow_async import AsyncUnitOfWork
from src.observability import increment_metric

router = APIRouter(prefix="/v1/webhooks", tags=["Webhooks"])
logger = logging.getLogger(__name__)

# Inline ledger sync after the legacy commit (plan §6.1.4); the rest is retried.
INLINE_SYNC_BUDGET_SECONDS = 3.0

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


def _get_feature_flag_reader():
    return get_feature_flag_reader()


def _get_entitlement_inbox_service():
    return get_entitlement_inbox_service()


@dataclass
class _LedgerWrite:
    """``ent_ledger_write`` state for one delivery (None when the flag is off)."""

    inbox: Any
    flags: Any
    payload: dict
    raw_body: bytes
    event_row_id: str | None = None
    parked: bool = False


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

    Every delivery and every non-2xx answer is counted, so auth and processing
    failures are visible while RevenueCat is still retrying.
    """
    increment_metric("webhook.revenuecat.received")
    try:
        return await _process_revenuecat_webhook(request, authorization)
    except HTTPException as exc:
        increment_metric(
            "webhook.revenuecat.non_2xx",
            attributes={"status": str(exc.status_code)},
        )
        raise
    except Exception:
        increment_metric("webhook.revenuecat.non_2xx", attributes={"status": "500"})
        raise


async def _process_revenuecat_webhook(
    request: Request,
    authorization: str | None,
) -> dict:
    auth_header = settings.REVENUECAT_WEBHOOK_AUTH_HEADER
    # Compare bytes: compare_digest raises TypeError on non-ASCII str, which
    # would turn a bad credential into a 500.
    if auth_header:
        if not authorization or not hmac.compare_digest(
            authorization.strip().encode(), auth_header.strip().encode()
        ):
            raise HTTPException(status_code=401, detail="Invalid authorization header")

    # Verify webhook is configured - reject if secret not set
    webhook_secret = settings.REVENUECAT_WEBHOOK_SECRET or ""
    if not webhook_secret and not auth_header:
        logger.error("RevenueCat webhook not configured - rejecting request")
        raise HTTPException(status_code=503, detail="Webhook not configured")

    # Verify authorization (constant-time comparison to prevent timing attacks)
    if not auth_header and not hmac.compare_digest(
        (authorization or "").encode(), webhook_secret.encode()
    ):
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

    ledger = await _ledger_write(request, payload)
    response = await _handle_native_event(
        event, event_type, affiliate_handler, event_publisher, ledger
    )
    if ledger is not None and ledger.event_row_id and not ledger.parked:
        await _sync_after_commit(ledger, event_type)
    return response


async def _handle_native_event(
    event: dict,
    event_type: str,
    affiliate_handler,
    event_publisher,
    ledger: _LedgerWrite | None,
) -> dict:
    """Legacy native path. With ``ledger`` None it is exactly the pre-ledger code."""
    park_reason: str | None = None
    # Get user
    async with AsyncUnitOfWork() as uow:
        if ledger is None:
            user = await find_user_for_revenuecat_event(uow, event)
        else:
            recorded = await ledger.inbox.record(uow, ledger.payload, ledger.raw_body)
            if recorded.settled:
                increment_metric(
                    "webhook.revenuecat.duplicate",
                    attributes={"event_type": event_type},
                )
                return {"status": "success", "duplicate": True}
            ledger.event_row_id = recorded.event_id
            user, park_reason = await _find_active_user(uow, event, ledger)
            if user is not None:
                await ledger.inbox.attach_user(uow, recorded.event_id, str(user.id))

        if event_type == "TRANSFER":
            if not user:
                logger.info("RevenueCat transfer ignored: target user not found")
                await _park(uow, ledger, park_reason, event_type)
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
                parked = await _park_unknown_user(uow, ledger, park_reason, event_type)
                if parked is not None:
                    return parked
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
                await _park(uow, ledger, park_reason, event_type)
                return {"status": "ignored", "reason": "user_not_found"}
            parked = await _park_unknown_user(uow, ledger, park_reason, event_type)
            if parked is not None:
                return parked
            raise HTTPException(status_code=404, detail="User not found")

        # Handle events — commit/rollback is owned by the AsyncUnitOfWork context manager
        if event_type == "INITIAL_PURCHASE":
            await handle_purchase(uow, user, event, affiliate_handler, event_publisher)

        elif event_type == "RENEWAL":
            await handle_renewal(uow, user, event, affiliate_handler, event_publisher)

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

    increment_metric(
        "webhook.revenuecat.processed",
        attributes={"event_type": event_type or "unknown", "status": "success"},
    )
    return {"status": "success"}


async def _ledger_write(request: Request, payload: dict) -> _LedgerWrite | None:
    """Ledger hooks for this delivery, or None while ``ent_ledger_write`` is off."""
    flags = _get_feature_flag_reader()
    if not await flags.is_enabled(FLAG_ENT_LEDGER_WRITE):
        return None
    try:
        raw_body = await request.body()
    except Exception:
        raw_body = json.dumps(payload, sort_keys=True).encode()
    return _LedgerWrite(
        inbox=_get_entitlement_inbox_service(),
        flags=flags,
        payload=payload,
        raw_body=raw_body,
    )


async def _find_active_user(uow, event: dict, ledger: _LedgerWrite):
    """Active users only: ledger identities first, then the legacy lookup.

    Returns ``(user, park_reason)``.
    """
    resolution = await ledger.inbox.resolve_users(uow, event)
    if resolution.primary_user_id:
        user = await load_user_by_id(uow, resolution.primary_user_id)
        if user is not None:
            return user, None
    # The legacy lookup also matches legacy subscription rows; it may return a
    # deleted (inactive) account, which must never be written again (D11).
    user = await find_user_for_revenuecat_event(uow, event)
    if user is not None and user.is_active:
        return user, None
    if user is not None:
        return None, ParkReason.DELETED_USER
    return None, resolution.park_reason or ParkReason.USER_NOT_FOUND


async def _park(
    uow, ledger: _LedgerWrite | None, reason: str | None, event_type: str
) -> None:
    """Park the inbox row as ``unmatched`` so a later sign-in can replay it."""
    if ledger is None or ledger.event_row_id is None:
        return
    reason = reason or ParkReason.USER_NOT_FOUND
    await ledger.inbox.park(uow, ledger.event_row_id, reason)
    ledger.parked = True
    increment_metric(
        "webhook.revenuecat.unmatched",
        attributes={"event_type": event_type, "reason": reason},
    )


async def _park_unknown_user(
    uow, ledger: _LedgerWrite | None, reason: str | None, event_type: str
) -> dict | None:
    """200 instead of the legacy 404, or None to keep the 404.

    A deleted account is always acknowledged (retrying cannot help). An
    unknown user is acknowledged only while ``ent_reconcile`` replays parked
    rows; otherwise RevenueCat's retries stay the recovery path.
    """
    if ledger is None:
        return None
    if reason == ParkReason.DELETED_USER:
        await _park(uow, ledger, reason, event_type)
        return {"status": "ignored", "reason": ParkReason.DELETED_USER.value}
    if not await ledger.flags.is_enabled(FLAG_ENT_RECONCILE):
        return None
    await _park(uow, ledger, reason, event_type)
    return {"status": "parked", "reason": ParkReason.USER_NOT_FOUND.value}


async def _sync_after_commit(ledger: _LedgerWrite, event_type: str) -> None:
    """Inline ledger sync in its own transactions. Never changes the response."""
    row_id = ledger.event_row_id
    if row_id is None:
        return
    try:
        async with asyncio.timeout(INLINE_SYNC_BUDGET_SECONDS):
            outcome = await ledger.inbox.process(row_id, lane=SyncLane.WEBHOOK)
    except TimeoutError:
        outcome = "timeout"
        try:
            await ledger.inbox.schedule_retry(row_id, "inline_sync_timeout")
        except Exception:
            logger.warning("Could not schedule inbox retry", exc_info=True)
    except Exception:
        outcome = "error"
        logger.warning("Inline entitlement sync failed", exc_info=True)
    increment_metric(
        "webhook.revenuecat.ledger_sync",
        attributes={"event_type": event_type, "outcome": outcome},
    )


@router.get("/revenuecat/health")
async def webhook_health():
    """Health check for webhook."""
    return {"status": "ok", "service": "revenuecat_webhook"}
