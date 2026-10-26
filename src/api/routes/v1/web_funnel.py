"""Possession-bound pre-checkout lead and claim endpoints."""

from hmac import compare_digest

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.base_dependencies import (
    get_subscription_service,
    get_web_funnel_redemption_service,
)
from src.api.dependencies.auth import verify_firebase_token_revocation_checked
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.web_funnel_redemption_session import (
    hydrate_custom_email,
    silent_login_token_allowed,
)
from src.api.schemas.request.web_funnel_claim_requests import (
    WebFunnelClaimCompleteRequest,
    WebFunnelClaimExchangeRequest,
    WebFunnelLeadCreateRequest,
    WebFunnelRedemptionFinalizeRequest,
    WebFunnelRedemptionPreflightRequest,
    WebFunnelRevenueCatCorrelationRequest,
)
from src.app.services.web_funnel_claim_common import (
    claim_not_found,
    standard_expires_at,
    utcnow,
)
from src.app.services.web_funnel_claim_completion import complete_claim, recover_claim
from src.app.services.web_funnel_claim_exchange import exchange_claim
from src.app.services.web_funnel_leads import (
    bind_revenuecat_customer,
    create_or_replay_lead,
    queue_claim_resend,
    require_access_key_hash,
    revoke_lead,
)
from src.app.services.web_funnel_redemption_verification import (
    RevenueCatVerificationState,
    verify_redeemed_customer,
)
from src.infra.config.settings import settings
from src.infra.database.config_async import get_async_db
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)

router = APIRouter(prefix="/v1/web-funnel", tags=["Web Funnel"])


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


def _require_fresh_token(token: dict) -> None:
    issued_at = token.get("iat")
    token_age = utcnow().timestamp() - issued_at if isinstance(issued_at, int) else -1
    if token_age < 0 or token_age > 600:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Fresh authentication required",
        )


def _is_supported_redemption_provider(
    provider: object, token: dict | None = None
) -> bool:
    return silent_login_token_allowed(provider, token)


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
    access_key_hash = require_access_key_hash(access_key)
    if not request_id or len(request_id) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request"
        )
    lead = await create_or_replay_lead(
        db,
        email=str(payload.email),
        access_key_hash=access_key_hash,
        request_id=request_id,
        snapshot=payload.payload.model_dump(mode="json"),
    )
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
    await revoke_lead(db, lead_id, access_key)
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
    access_key_hash = require_access_key_hash(access_key)
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
    if (
        not settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT
        or not settings.REVENUECAT_SECRET_API_KEY
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    lead = await bind_revenuecat_customer(
        db,
        _get_web_funnel_subscription_service(),
        lead_id,
        access_key,
        app_user_id=payload.app_user_id,
        redemption_link_hash=payload.redemption_link_hash,
        environment=settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT,
    )
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
    _require_fresh_token(token)
    token = await hydrate_custom_email(token)
    uid, email = token.get("uid"), token.get("email")
    provider = (token.get("firebase") or {}).get("sign_in_provider")
    if (
        not isinstance(uid, str)
        or not isinstance(email, str)
        or not token.get("email_verified")
        or not _is_supported_redemption_provider(provider, token)
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
async def finalize_revenuecat_redemption(
    request: Request,
    payload: WebFunnelRedemptionFinalizeRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    token: dict = Depends(verify_firebase_token_revocation_checked),
    db: AsyncSession = Depends(get_async_db),
):
    """Finalize one provider-verified redemption from a fresh Firebase identity."""
    if not payload.confirm_apply_purchase:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Purchase confirmation required",
        )
    _require_fresh_token(token)
    token = await hydrate_custom_email(token)
    uid, email = token.get("uid"), token.get("email")
    provider = (token.get("firebase") or {}).get("sign_in_provider")
    if (
        not isinstance(uid, str)
        or not _is_supported_redemption_provider(provider, token)
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
    service = _get_web_funnel_subscription_service()
    binding = await db.scalar(
        select(WebFunnelRedemption).where(
            WebFunnelRedemption.environment
            == settings.WEB_FUNNEL_REVENUECAT_ENVIRONMENT,
            WebFunnelRedemption.redemption_link_hash == payload.redemption_link_hash,
            WebFunnelRedemption.preflight_uid == uid,
        )
    )
    if not binding:
        raise claim_not_found()

    subscriber = await service.get_subscriber_info(uid)
    original_app_user_id = _subscriber_original_app_user_id(subscriber)
    if (
        not original_app_user_id
        or verify_redeemed_customer(
            subscriber,
            original_app_user_id=original_app_user_id,
            allowed_product_ids={binding.product_id},
        ).state
        is not RevenueCatVerificationState.VERIFIED
    ):
        raise claim_not_found()

    known_app_user_ids = {
        binding.original_app_user_id,
        binding.verified_app_user_id,
        *(binding.provider_app_user_ids or []),
    }
    if original_app_user_id not in known_app_user_ids:
        # RevenueCat may report an older mobile anonymous ID as canonical after
        # the web checkout customer is aliased. Confirm both IDs resolve to the
        # same live customer and the exact product bound to this checkout.
        source_subscriber = await service.get_subscriber_info(
            binding.original_app_user_id
        )
        source_original_app_user_id = _subscriber_original_app_user_id(
            source_subscriber
        )
        if (
            not source_original_app_user_id
            or source_original_app_user_id != original_app_user_id
            or verify_redeemed_customer(
                source_subscriber,
                original_app_user_id=source_original_app_user_id,
                allowed_product_ids={binding.product_id},
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
        verified_provider_app_user_ids={original_app_user_id},
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
    lead, retry_after_seconds = await queue_claim_resend(db, lead_id, access_key)
    return {**_projection(lead), "retry_after_seconds": retry_after_seconds}


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
