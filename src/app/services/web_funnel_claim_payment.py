"""Durable RevenueCat reconciliation and token-safe external outbox workers."""

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.services.web_funnel_claim_common import (
    CLAIM_TTL,
    hash_secret,
    is_active_standard,
    new_secret,
    utcnow,
)
from src.infra.config.settings import settings
from src.infra.database.models.web_funnel_claim import (
    WebFunnelClaim,
    WebFunnelLead,
    WebFunnelOutbox,
    WebFunnelProviderEvent,
)


def _exact_lead_id(event: dict) -> str | None:
    value = event.get("app_user_id")
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        return None


def _matches_revenuecat_environment(environment: object) -> bool:
    """Fail closed unless this webhook belongs to the configured environment."""
    return isinstance(environment, str) and bool(
        settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT
    ) and environment == settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT


async def reconcile_revenuecat_event(db: AsyncSession, event: dict, subscriber: dict | None) -> bool:
    """Handle web leads only; caller continues the native webhook path unchanged."""
    event_id = event.get("id")
    lead_id = _exact_lead_id(event)
    if not isinstance(event_id, str) or not lead_id:
        return False
    lead = await db.get(WebFunnelLead, lead_id, with_for_update=True)
    if not lead:
        return False
    existing = await db.scalar(select(WebFunnelProviderEvent).where(WebFunnelProviderEvent.provider_event_id == event_id))
    if existing:
        return True
    inbox = WebFunnelProviderEvent(id=str(uuid.uuid4()), provider_event_id=event_id, event_type=str(event.get("type", "unknown")), lead_id=lead_id, payload={"environment": event.get("environment"), "product_id": event.get("product_id")}, created_at=utcnow())
    db.add(inbox)
    if not _matches_revenuecat_environment(event.get("environment")):
        await db.commit()
        return True
    if subscriber is None:
        db.add(WebFunnelOutbox(idempotency_key=f"revenuecat-reconcile:{event_id}", job_type="revenuecat_reconcile", payload={"provider_event_id": event_id, "lead_id": lead_id}, status="pending", attempts=0, next_attempt_at=utcnow()))
        await db.commit()
        return True
    if event.get("type") == "REFUND" or not is_active_standard(subscriber):
        lead.status, lead.access_sync_status = "refunded", "refunded"
        for claim in (await db.scalars(select(WebFunnelClaim).where(WebFunnelClaim.lead_id == lead.id, WebFunnelClaim.consumed_at.is_(None)).with_for_update())).all():
            claim.revoked_at = utcnow()
    elif lead.status not in {"claimed", "refunded"}:
        lead.status, lead.payment_verified_at = "payment_verified", utcnow()
        if settings.WEB_FUNNEL_LEGACY_CLAIM_ENABLED:
            generation = await next_claim_generation(db, lead.id)
            db.add(WebFunnelOutbox(idempotency_key=f"claim-email:{lead.id}:{generation}", job_type="claim_email", payload={"lead_id": lead.id, "generation": generation}, status="pending", attempts=0, next_attempt_at=utcnow()))
            lead.status = "email_queued"
    await db.commit()
    return True


async def process_revenuecat_reconcile(
    db: AsyncSession, outbox: WebFunnelOutbox, subscription_service: object
) -> bool:
    """Retry an authoritative customer fetch after a webhook-time provider outage."""
    lead = await db.get(WebFunnelLead, outbox.payload["lead_id"], with_for_update=True)
    if not lead or lead.status in {"refunded", "revoked", "claimed"}:
        outbox.status, outbox.completed_at = "completed", utcnow()
        await db.commit()
        return True
    event = await db.scalar(
        select(WebFunnelProviderEvent).where(
            WebFunnelProviderEvent.provider_event_id
            == outbox.payload["provider_event_id"]
        )
    )
    if not event:
        outbox.status, outbox.completed_at = "completed", utcnow()
        await db.commit()
        return True
    if not _matches_revenuecat_environment(event.payload.get("environment")):
        outbox.status, outbox.completed_at = "completed", utcnow()
        await db.commit()
        return True
    if event.event_type == "REFUND":
        await _revoke_unconsumed_claims(db, lead)
        lead.status, lead.access_sync_status = "refunded", "refunded"
        event.processed_at = utcnow()
        outbox.status, outbox.completed_at = "completed", utcnow()
        await db.commit()
        return True
    subscriber = await subscription_service.get_subscriber_info(lead.id)
    if not is_active_standard(subscriber):
        if event.event_type in {"CANCELLATION", "EXPIRATION"}:
            await _revoke_unconsumed_claims(db, lead)
            lead.status, lead.access_sync_status = "expired", "refunded"
            event.processed_at = utcnow()
            outbox.status, outbox.completed_at = "completed", utcnow()
            await db.commit()
            return True
        outbox.attempts += 1
        outbox.next_attempt_at = utcnow() + timedelta(minutes=min(60, 2 ** int(outbox.attempts)))
        await db.commit()
        return False
    lead.status, lead.payment_verified_at, event.processed_at, outbox.status, outbox.completed_at = (
        "payment_verified",
        utcnow(),
        utcnow(),
        "completed",
        utcnow(),
    )
    if settings.WEB_FUNNEL_LEGACY_CLAIM_ENABLED:
        generation = await next_claim_generation(db, lead.id)
        db.add(WebFunnelOutbox(idempotency_key=f"claim-email:{lead.id}:{generation}", job_type="claim_email", payload={"lead_id": lead.id, "generation": generation}, status="pending", attempts=0, next_attempt_at=utcnow()))
        lead.status = "email_queued"
    await db.commit()
    return True


async def _revoke_unconsumed_claims(db: AsyncSession, lead: WebFunnelLead) -> None:
    claims = await db.scalars(
        select(WebFunnelClaim)
        .where(
            WebFunnelClaim.lead_id == lead.id,
            WebFunnelClaim.consumed_at.is_(None),
            WebFunnelClaim.revoked_at.is_(None),
        )
        .with_for_update()
    )
    for claim in claims.all():
        claim.revoked_at = utcnow()


async def next_claim_generation(db: AsyncSession, lead_id: str) -> int:
    """Allocate a generation under the lead lock, including queued email work."""
    latest_claim = await db.scalar(
        select(WebFunnelClaim.generation)
        .where(WebFunnelClaim.lead_id == lead_id)
        .order_by(WebFunnelClaim.generation.desc())
        .limit(1)
    )
    queued = await db.scalars(
        select(WebFunnelOutbox).where(
            WebFunnelOutbox.idempotency_key.like(f"claim-email:{lead_id}:%")
        )
    )
    queued_generations = [
        int(row.payload["generation"])
        for row in queued.all()
        if isinstance(row.payload.get("generation"), int)
    ]
    return max([int(latest_claim or 0), *queued_generations], default=0) + 1


async def process_claim_email(db: AsyncSession, outbox: WebFunnelOutbox, send: object) -> None:
    """Mint and send a link in memory. A post-send crash is safely recoverable by resend."""
    lead = await db.get(WebFunnelLead, outbox.payload["lead_id"], with_for_update=True)
    if not lead or lead.status in {"refunded", "revoked"}:
        outbox.status, outbox.completed_at = "completed", utcnow()
        await db.commit()
        return
    token = new_secret()
    result = await send(lead.email, token, lead.id)  # token intentionally has no log/persistence path
    if not result:
        outbox.attempts += 1
        outbox.next_attempt_at = utcnow() + timedelta(minutes=min(60, 2 ** outbox.attempts))
        await db.commit()
        return
    generation = int(outbox.payload["generation"])
    active_claims = await db.scalars(
        select(WebFunnelClaim)
        .where(
            WebFunnelClaim.lead_id == lead.id,
            WebFunnelClaim.consumed_at.is_(None),
            WebFunnelClaim.revoked_at.is_(None),
        )
        .with_for_update()
    )
    for active_claim in active_claims.all():
        active_claim.revoked_at = utcnow()
    db.add(WebFunnelClaim(lead_id=lead.id, generation=generation, magic_token_hash=hash_secret(token), expires_at=utcnow() + CLAIM_TTL))
    outbox.status, outbox.completed_at, lead.status = "completed", utcnow(), "email_queued"
    await db.commit()
