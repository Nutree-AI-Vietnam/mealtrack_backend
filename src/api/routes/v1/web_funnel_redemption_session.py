"""Unauthenticated hash → one-time custom token. Do not grow web_funnel.py."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.middleware.rate_limit import get_ip_only_rate_limit_key, limiter
from src.api.schemas.request.web_funnel_claim_requests import (
    WebFunnelRedemptionPreflightRequest,
)
from src.app.services.web_funnel_claim_common import claim_not_found, utcnow
from src.infra.config.settings import settings
from src.infra.database.config_async import get_async_db
from src.infra.database.models.user.user import User
from src.infra.database.models.web_funnel_claim import (
    WebFunnelLead,
    WebFunnelRedemption,
)
from src.infra.services.web_funnel_redemption_identity import (
    SILENT_LOGIN_CLAIM,
    RedemptionIdentityUnavailable,
    WebFunnelRedemptionIdentityService,
)

router = APIRouter(prefix="/v1/web-funnel", tags=["Web Funnel"])
_TERMINAL = frozenset({"refunded", "revoked", "conflict"})


def _identity_service() -> WebFunnelRedemptionIdentityService:
    return WebFunnelRedemptionIdentityService()


def silent_login_token_allowed(provider: object, token: dict | None) -> bool:
    if provider in {"google.com", "apple.com", "password"}:
        return True
    if not settings.WEB_FUNNEL_SILENT_LOGIN_ENABLED or provider != "custom":
        return False
    return bool(token) and token.get(SILENT_LOGIN_CLAIM) in (1, True)


@router.post("/redemptions/session")
@limiter.limit("5/minute", key_func=get_ip_only_rate_limit_key)
async def create_redemption_session(
    request: Request,
    payload: WebFunnelRedemptionPreflightRequest,
    db: AsyncSession = Depends(get_async_db),
    identity: WebFunnelRedemptionIdentityService = Depends(_identity_service),
):
    if not (
        settings.WEB_FUNNEL_REDEMPTION_ENABLED
        and settings.WEB_FUNNEL_SILENT_LOGIN_ENABLED
    ):
        raise claim_not_found()
    binding = await db.scalar(
        select(WebFunnelRedemption)
        .where(WebFunnelRedemption.redemption_link_hash == payload.redemption_link_hash)
        .with_for_update()
    )
    if not binding or binding.finalized_uid:
        raise claim_not_found()
    if binding.silent_login_minted_at is not None:
        raise claim_not_found()
    lead = await db.get(WebFunnelLead, binding.lead_id, with_for_update=True)
    if not lead or lead.status in _TERMINAL:
        raise claim_not_found()
    owner = await db.scalar(
        select(User).where(func.lower(User.email) == lead.email.strip().lower())
    )
    try:
        token = await identity.mint_for_email(
            lead.email, mealtrack_uid=owner.firebase_uid if owner else None
        )
    except RedemptionIdentityUnavailable:
        raise claim_not_found() from None
    binding.silent_login_minted_at = utcnow()
    binding.silent_login_generation = (binding.silent_login_generation or 0) + 1
    await db.commit()
    return {"version": "redemption_session_v1", "custom_token": token}


async def email_for_custom_token(token: dict) -> str | None:
    uid = token.get("uid")
    if not isinstance(uid, str):
        return None
    return await _identity_service().verified_email_for_uid(uid)


async def hydrate_custom_email(token: dict) -> dict:
    provider = (token.get("firebase") or {}).get("sign_in_provider")
    email = token.get("email")
    if provider != "custom" or (isinstance(email, str) and token.get("email_verified")):
        return token
    filled = await email_for_custom_token(token)
    return {**token, "email": filled, "email_verified": True} if filled else token
