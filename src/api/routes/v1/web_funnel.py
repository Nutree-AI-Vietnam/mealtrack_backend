"""Possession-bound pre-checkout lead and claim endpoints."""

import hashlib
import json
from hmac import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.base_dependencies import (
    get_subscription_service,
    get_web_funnel_redemption_service,
)
from src.api.dependencies.auth import verify_firebase_token_revocation_checked
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.web_funnel_claim_requests import (
    WebFunnelClaimCompleteRequest,
    WebFunnelClaimExchangeRequest,
    WebFunnelLeadCreateRequest,
    WebFunnelRedemptionFinalizeRequest,
    WebFunnelRedemptionPreflightRequest,
    WebFunnelRevenueCatCorrelationRequest,
)
from src.app.services.web_funnel_claim_common import (
    RESEND_COOLDOWN,
    claim_conflict,
    claim_not_found,
    standard_expires_at,
    utcnow,
)
from src.app.services.web_funnel_claim_completion import complete_claim, recover_claim
from src.app.services.web_funnel_claim_exchange import exchange_claim
from src.app.services.web_funnel_claim_payment import next_claim_generation
from src.app.services.web_funnel_redemption_verification import (
    RevenueCatVerificationState,
    verify_bound_web_customer,
    verify_redeemed_customer,
)
from src.infra.config.settings import settings
from src.infra.database.config_async import get_async_db
from src.infra.database.models.web_funnel_claim import (
    WebFunnelClaim,
    WebFunnelLead,
    WebFunnelOutbox,
    WebFunnelRedemption,
)

router = APIRouter(prefix="/v1/web-funnel", tags=["Web Funnel"])


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _masked_email(email: str) -> str:
    local, domain = email.split("@", maxsplit=1)
    return f"{local[:1]}***@{domain}"


def _projection(lead: WebFunnelLead) -> dict[str, str | int | None]:
    projection: dict[str, str | int | None] = {
        "lead_id": lead.id,
        "masked_email": _masked_email(lead.email),
        "status": lead.status,
    }
    if lead.status in {"email_queued", "claim_reserved", "claimed", "refunded"}:
        projection["access_status"] = lead.access_sync_status
    return projection


def _require_bff_request(origin: str | None, credential: str | None) -> None:
    """Require server-held BFF proof; Origin only narrows browser-origin use."""
    if (
        not settings.WEB_FUNNEL_BFF_SHARED_SECRET
        or not credential
        or not compare_digest(credential, settings.WEB_FUNNEL_BFF_SHARED_SECRET)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if (
        settings.WEB_FUNNEL_BFF_ORIGIN
        and origin
        and origin != settings.WEB_FUNNEL_BFF_ORIGIN
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _require_access_key(key: str | None) -> str:
    if not key or len(key) < 32:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized"
        )
    return _hash(key)


def _require_fresh_token(token: dict) -> None:
    issued_at = token.get("iat")
    token_age = utcnow().timestamp() - issued_at if isinstance(issued_at, int) else -1
    if token_age < 0 or token_age > 600:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Fresh authentication required",
        )


def _is_supported_redemption_provider(provider: object) -> bool:
    # Firebase represents passwordless Email Link sign-in as the password
    # provider. Email matching and email_verified remain mandatory below.
    return provider in {"google.com", "apple.com", "password"}


def _require_legacy_claim_enabled() -> None:
    if not settings.WEB_FUNNEL_LEGACY_CLAIM_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _get_web_funnel_subscription_service():
    """Resolve RevenueCat through the API composition boundary."""
    return get_subscription_service()


def _subscriber_original_app_user_id(subscriber: dict | None) -> str | None:
    customer = (subscriber or {}).get("subscriber")
    original = (
        customer.get("original_app_user_id") if isinstance(customer, dict) else None
    )
    return original if isinstance(original, str) and original else None


@router.post("/leads", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_lead(
    request: Request,
    payload: WebFunnelLeadCreateRequest,
    access_key: str | None = Header(default=None, alias="X-Lead-Access-Key"),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
    origin: str | None = Header(default=None, alias="Origin"),
    bff_credential: str | None = Header(default=None, alias="X-Web-Funnel-BFF-Token"),
    db: AsyncSession = Depends(get_async_db),
):
    """Create/replay a lead without exposing raw email or a browser capability."""
    _require_bff_request(origin, bff_credential)
    access_key_hash = _require_access_key(access_key)
    if not request_id or len(request_id) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request"
        )

    existing = await db.scalar(
        select(WebFunnelLead).where(WebFunnelLead.request_id == request_id)
    )
    if existing:
        if compare_digest(existing.access_key_hash, access_key_hash):
            return _projection(existing)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    snapshot = payload.payload.model_dump(mode="json")
    lead = WebFunnelLead(
        email=str(payload.email).lower(),
        access_key_hash=access_key_hash,
        request_id=request_id,
        snapshot_version="web_onboarding_snapshot_v1",
        snapshot=snapshot,
        snapshot_hash=_hash(
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
            return _projection(existing)
        raise claim_not_found() from None
    except Exception:
        await db.rollback()
        raise
    return _projection(lead)


@router.post("/leads/{lead_id}/reset")
@limiter.limit("5/minute")
async def reset_lead(
    request: Request,
    lead_id: str,
    access_key: str | None = Header(default=None, alias="X-Lead-Access-Key"),
    db: AsyncSession = Depends(get_async_db),
):
    """Revoke the browser capability's unpaid draft without revealing ownership."""
    lead = await db.get(WebFunnelLead, lead_id, with_for_update=True)
    if not lead or not compare_digest(
        lead.access_key_hash, _require_access_key(access_key)
    ):
        raise claim_not_found()
    if lead.status not in {"draft", "checkout_started"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Lead unavailable"
        )
    lead.revoked_at, lead.status = utcnow(), "revoked"
    await db.commit()
    return {"status": "revoked"}


@router.get("/leads/{lead_id}/status")
@limiter.limit("30/minute")
async def get_lead_status(
    request: Request,
    lead_id: str,
    access_key: str | None = Header(default=None, alias="X-Lead-Access-Key"),
    db: AsyncSession = Depends(get_async_db),
):
    """Return only a possession-bound, safe lead status projection."""
    access_key_hash = _require_access_key(access_key)
    lead = await db.get(WebFunnelLead, lead_id)
    if not lead or not compare_digest(lead.access_key_hash, access_key_hash):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _projection(lead)


@router.post("/leads/{lead_id}/revenuecat-correlation")
@limiter.limit("5/minute")
async def correlate_revenuecat_customer(
    request: Request,
    lead_id: str,
    payload: WebFunnelRevenueCatCorrelationRequest,
    access_key: str | None = Header(default=None, alias="X-Lead-Access-Key"),
    origin: str | None = Header(default=None, alias="Origin"),
    bff_credential: str | None = Header(default=None, alias="X-Web-Funnel-BFF-Token"),
    db: AsyncSession = Depends(get_async_db),
):
    """Bind an anonymous web customer only after a private provider read."""
    _require_bff_request(origin, bff_credential)
    if not settings.WEB_FUNNEL_REDEMPTION_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if (
        not settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT
        or not settings.REVENUECAT_SECRET_API_KEY
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    lead = await db.get(WebFunnelLead, lead_id, with_for_update=True)
    if not lead or not compare_digest(
        lead.access_key_hash, _require_access_key(access_key)
    ):
        raise claim_not_found()
    subscriber = await _get_web_funnel_subscription_service().get_subscriber_info(
        payload.app_user_id
    )
    verification = verify_bound_web_customer(
        subscriber,
        original_app_user_id=payload.app_user_id,
    )
    if verification.state is not RevenueCatVerificationState.VERIFIED:
        raise claim_not_found()
    existing = await db.scalar(
        select(WebFunnelRedemption)
        .where(WebFunnelRedemption.lead_id == lead.id)
        .with_for_update()
    )
    if existing is None and not settings.WEB_FUNNEL_CHECKOUT_ADMISSION_ENABLED:
        # New checkout admission off: paid recovery for existing rows still works.
        raise claim_not_found()
    if existing:
        if (
            existing.original_app_user_id != payload.app_user_id
            or existing.environment != settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT
        ):
            raise claim_conflict()
    else:
        existing = WebFunnelRedemption(
            lead_id=lead.id,
            provider="revenuecat",
            environment=settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT,
            project="default",
            original_app_user_id=payload.app_user_id,
            verified_app_user_id=payload.app_user_id,
            entitlement_id="standard",
            product_id=verification.product_id or "",
            verified_at=utcnow(),
            redemption_link_hash=payload.redemption_link_hash,
        )
        db.add(existing)
    if (
        existing.redemption_link_hash
        and existing.redemption_link_hash != payload.redemption_link_hash
    ):
        raise claim_conflict()
    existing.redemption_link_hash = payload.redemption_link_hash
    lead.payment_verified_at = utcnow()
    if lead.status in {"draft", "checkout_started"}:
        lead.status = "payment_verified"
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise claim_conflict() from None
    return _projection(lead)


@router.post("/redemptions/preflight")
@limiter.limit("5/minute")
async def preflight_revenuecat_redemption(
    request: Request,
    payload: WebFunnelRedemptionPreflightRequest,
    token: dict = Depends(verify_firebase_token_revocation_checked),
    db: AsyncSession = Depends(get_async_db),
):
    """Bind a matching verified Firebase identity before redemption is consumed."""
    if not settings.WEB_FUNNEL_REDEMPTION_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    _require_fresh_token(token)
    uid, email = token.get("uid"), token.get("email")
    provider = (token.get("firebase") or {}).get("sign_in_provider")
    if (
        not isinstance(uid, str)
        or not isinstance(email, str)
        or not token.get("email_verified")
        or not _is_supported_redemption_provider(provider)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Verified email required"
        )
    eligible = await get_web_funnel_redemption_service().preflight(
        db,
        uid=uid,
        email=email,
        redemption_link_hash=payload.redemption_link_hash,
    )
    if not eligible:
        raise claim_not_found()
    return {"version": "redemption_preflight_v1", "eligible": True}


@router.post("/redemptions/finalize")
@limiter.limit("5/minute")
async def finalize_revenuecat_redemption(
    request: Request,
    payload: WebFunnelRedemptionFinalizeRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    token: dict = Depends(verify_firebase_token_revocation_checked),
    db: AsyncSession = Depends(get_async_db),
):
    """Finalize one provider-verified redemption from a fresh Firebase identity."""
    if not settings.WEB_FUNNEL_REDEMPTION_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not payload.confirm_apply_purchase:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Purchase confirmation required",
        )
    _require_fresh_token(token)
    uid, email = token.get("uid"), token.get("email")
    provider = (token.get("firebase") or {}).get("sign_in_provider")
    if (
        not isinstance(uid, str)
        or not _is_supported_redemption_provider(provider)
        or not isinstance(email, str)
        or not token.get("email_verified")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Verified email required"
        )
    if not idempotency_key or not 16 <= len(idempotency_key) <= 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid idempotency key"
        )
    if (
        not settings.REVENUECAT_SECRET_API_KEY
        or not settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    subscriber = await _get_web_funnel_subscription_service().get_subscriber_info(uid)
    original_app_user_id = _subscriber_original_app_user_id(subscriber)
    if (
        not original_app_user_id
        or verify_redeemed_customer(
            subscriber,
            original_app_user_id=original_app_user_id,
        ).state
        is not RevenueCatVerificationState.VERIFIED
    ):
        raise claim_not_found()
    response.headers["Cache-Control"] = "no-store"
    return await get_web_funnel_redemption_service().finalize(
        db,
        uid=uid,
        email=email,
        original_app_user_id=original_app_user_id,
        redemption_link_hash=payload.redemption_link_hash,
        idempotency_key=idempotency_key,
        environment=settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT,
        auth_provider=provider,
        expires_at=standard_expires_at(subscriber),
    )


@router.post("/leads/{lead_id}/resend")
@limiter.limit("3/minute")
async def resend_claim(
    request: Request,
    lead_id: str,
    access_key: str | None = Header(default=None, alias="X-Lead-Access-Key"),
    db: AsyncSession = Depends(get_async_db),
):
    """Queue a fresh link generation; old unconsumed credentials are revoked."""
    _require_legacy_claim_enabled()
    lead = await db.get(WebFunnelLead, lead_id, with_for_update=True)
    if not lead or not compare_digest(
        lead.access_key_hash, _require_access_key(access_key)
    ):
        raise claim_not_found()
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
        return {
            **_projection(lead),
            "retry_after_seconds": int(
                (latest.created_at + RESEND_COOLDOWN - utcnow()).total_seconds()
            ),
        }
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
    return {
        **_projection(lead),
        "retry_after_seconds": int(RESEND_COOLDOWN.total_seconds()),
    }


@router.post("/claims/exchange")
@limiter.limit("5/minute")
async def exchange(
    request: Request,
    payload: WebFunnelClaimExchangeRequest,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
):
    _require_legacy_claim_enabled()
    response.headers["Cache-Control"] = "no-store"
    return await exchange_claim(db, payload.magic_token, payload.client_retry_secret)


@router.post("/claims/complete")
@limiter.limit("10/minute")
async def complete(
    request: Request,
    payload: WebFunnelClaimCompleteRequest,
    response: Response,
    token: dict = Depends(verify_firebase_token_revocation_checked),
    db: AsyncSession = Depends(get_async_db),
):
    _require_legacy_claim_enabled()
    response.headers["Cache-Control"] = "no-store"
    _require_fresh_token(token)
    return await complete_claim(
        db, token.get("uid", ""), token.get("email"), payload.exchange_token
    )


@router.get("/claims/recovery")
@limiter.limit("10/minute")
async def recovery(
    request: Request,
    response: Response,
    token: dict = Depends(verify_firebase_token_revocation_checked),
    db: AsyncSession = Depends(get_async_db),
):
    _require_legacy_claim_enabled()
    response.headers["Cache-Control"] = "no-store"
    _require_fresh_token(token)
    reservation_id = token.get("wf_reservation")
    generation = token.get("wf_generation")
    return await recover_claim(
        db,
        token.get("uid", ""),
        reservation_id,
        generation if isinstance(generation, int) else None,
    )
