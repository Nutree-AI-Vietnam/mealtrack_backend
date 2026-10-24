"""Possession-bound lead writes for the pre-checkout web funnel.

Like the other web-funnel services, each write owns its transaction on the
request session. Routes keep the BFF, header and token checks and the safe
projection of the lead.
"""

import json
from hmac import compare_digest
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.services.web_funnel_claim_common import (
    RESEND_COOLDOWN,
    claim_conflict,
    claim_not_found,
    hash_secret,
    utcnow,
)
from src.app.services.web_funnel_claim_payment import next_claim_generation
from src.app.services.web_funnel_redemption_verification import (
    RevenueCatVerificationState,
    verify_bound_web_customer,
)
from src.domain.ports.subscription_service_port import SubscriptionServicePort
from src.infra.database.models.web_funnel_claim import (
    WebFunnelClaim,
    WebFunnelLead,
    WebFunnelOutbox,
    WebFunnelRedemption,
)


def require_access_key_hash(key: str | None) -> str:
    if not key or len(key) < 32:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    return hash_secret(key)


async def _lock_owned_lead(
    db: AsyncSession, lead_id: str, access_key: str | None
) -> WebFunnelLead:
    lead = await db.get(WebFunnelLead, lead_id, with_for_update=True)
    if not lead or not compare_digest(
        lead.access_key_hash, require_access_key_hash(access_key)
    ):
        raise claim_not_found()
    return lead


async def create_or_replay_lead(
    db: AsyncSession,
    *,
    email: str,
    access_key_hash: str,
    request_id: str,
    snapshot: dict[str, Any],
) -> WebFunnelLead:
    """Create or replay a lead; another capability's replay is not found."""
    existing = await db.scalar(
        select(WebFunnelLead).where(WebFunnelLead.request_id == request_id)
    )
    if existing:
        if compare_digest(existing.access_key_hash, access_key_hash):
            return existing
        raise claim_not_found()

    lead = WebFunnelLead(
        email=email.lower(),
        access_key_hash=access_key_hash,
        request_id=request_id,
        snapshot_version="web_onboarding_snapshot_v1",
        snapshot=snapshot,
        snapshot_hash=hash_secret(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        ),
        status="draft",
        revision=1,
        access_sync_status="pending",
    )
    db.add(lead)
    try:
        await db.commit()
        await db.refresh(lead)
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(WebFunnelLead).where(WebFunnelLead.request_id == request_id)
        )
        if existing and compare_digest(existing.access_key_hash, access_key_hash):
            return existing
        raise claim_not_found() from None
    except Exception:
        await db.rollback()
        raise
    return lead


async def revoke_lead(db: AsyncSession, lead_id: str, access_key: str | None) -> None:
    """Revoke the browser capability's unpaid draft."""
    lead = await _lock_owned_lead(db, lead_id, access_key)
    if lead.status not in {"draft", "checkout_started"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Lead unavailable"
        )
    lead.revoked_at, lead.status = utcnow(), "revoked"
    await db.commit()


async def bind_revenuecat_customer(
    db: AsyncSession,
    subscriptions: SubscriptionServicePort,
    lead_id: str,
    access_key: str | None,
    *,
    app_user_id: str,
    redemption_link_hash: str,
    environment: str,
) -> WebFunnelLead:
    """Bind an anonymous web customer only after a private provider read."""
    lead = await _lock_owned_lead(db, lead_id, access_key)
    subscriber = await subscriptions.get_subscriber_info(app_user_id)
    verification = verify_bound_web_customer(
        subscriber,
        original_app_user_id=app_user_id,
    )
    if verification.state is not RevenueCatVerificationState.VERIFIED:
        raise claim_not_found()
    existing = await db.scalar(
        select(WebFunnelRedemption)
        .where(WebFunnelRedemption.lead_id == lead.id)
        .with_for_update()
    )
    if existing:
        if (
            existing.original_app_user_id != app_user_id
            or existing.environment != environment
        ):
            raise claim_conflict()
    else:
        existing = WebFunnelRedemption(
            lead_id=lead.id,
            provider="revenuecat",
            environment=environment,
            project="default",
            original_app_user_id=app_user_id,
            verified_app_user_id=app_user_id,
            entitlement_id="standard",
            product_id=verification.product_id or "",
            verified_at=utcnow(),
            redemption_link_hash=redemption_link_hash,
        )
        db.add(existing)
    if (
        existing.redemption_link_hash
        and existing.redemption_link_hash != redemption_link_hash
    ):
        raise claim_conflict()
    existing.redemption_link_hash = redemption_link_hash
    lead.payment_verified_at = utcnow()
    if lead.status in {"draft", "checkout_started"}:
        lead.status = "payment_verified"
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise claim_conflict() from None
    return lead


async def queue_claim_resend(
    db: AsyncSession, lead_id: str, access_key: str | None
) -> tuple[WebFunnelLead, int]:
    """Queue a fresh link generation and revoke old unconsumed credentials.

    Returns the lead and the seconds until another resend is allowed.
    """
    lead = await _lock_owned_lead(db, lead_id, access_key)
    if lead.status not in {"payment_verified", "email_queued", "claim_reserved"}:
        raise claim_not_found()
    claims = (
        await db.scalars(
            select(WebFunnelClaim)
            .where(WebFunnelClaim.lead_id == lead.id)
            .order_by(WebFunnelClaim.generation.desc())
            .with_for_update()
        )
    ).all()
    latest = claims[0] if claims else None
    if latest and latest.created_at + RESEND_COOLDOWN > utcnow():
        return lead, int(
            (latest.created_at + RESEND_COOLDOWN - utcnow()).total_seconds()
        )
    for claim in claims:
        if not claim.consumed_at and not claim.revoked_at:
            claim.revoked_at = utcnow()
    generation = await next_claim_generation(db, lead.id)
    # The worker generates the raw link only in memory and persists its hash after
    # sending. Neither this outbox nor any request payload can leak the secret.
    db.add(
        WebFunnelOutbox(
            idempotency_key=f"claim-email:{lead.id}:{generation}",
            job_type="claim_email",
            payload={"lead_id": lead.id, "generation": generation},
            status="pending",
            attempts=0,
            next_attempt_at=utcnow(),
        )
    )
    lead.status = "email_queued"
    await db.commit()
    return lead, int(RESEND_COOLDOWN.total_seconds())
