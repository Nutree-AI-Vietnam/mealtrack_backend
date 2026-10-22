"""In-app MoMo one-time pass checkout and the MoMo IPN webhook."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from src.api.dependencies.auth import get_current_user_id, verify_firebase_token
from src.api.middleware.rate_limit import limiter
from src.bootstrap.momo_payments import (
    MomoBuyer,
    MomoGatewayError,
    MomoNotConfigured,
    get_momo_pass_service,
)
from src.domain.model.payments.momo_pass import (
    MomoPayMethod,
    MomoPlanKey,
    get_momo_pass_plan,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/payments/momo", tags=["MoMo Payments"])
webhook_router = APIRouter(prefix="/v1/webhooks/momo", tags=["Webhooks"])


class MomoCheckoutRequest(BaseModel):
    plan: MomoPlanKey
    lang: Literal["vi", "en"] = "vi"
    # wallet: MoMo app (deeplink) or QR; atm: domestic card on MoMo's page.
    method: MomoPayMethod = MomoPayMethod.WALLET


# Sign-in methods that prove the user owns the mailbox. Custom tokens, the web
# funnel's silent login included, never vouch for an email.
_PROVEN_EMAIL_PROVIDERS = frozenset({"google.com", "apple.com", "password"})


def momo_pass_email(token: dict) -> str | None:
    """The email that may unlock quiz passes and price app passes, or None."""
    firebase = token.get("firebase")
    provider = firebase.get("sign_in_provider") if isinstance(firebase, dict) else None
    email = token.get("email")
    if (
        token.get("email_verified") is True
        and provider in _PROVEN_EMAIL_PROVIDERS
        and isinstance(email, str)
        and email
    ):
        return email
    return None


def _app_buyer(user_id: str, token: dict) -> MomoBuyer:
    return MomoBuyer(channel="app", user_id=user_id, email=momo_pass_email(token))


def checkout_error(error: Exception) -> HTTPException:
    if isinstance(error, MomoNotConfigured):
        # Setting names go to the server log only, never to the caller.
        logger.error("MoMo checkout is not configured: %s", error.missing)
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MoMo checkout is unavailable.",
        )
    logger.warning("MoMo checkout failed: %s", error)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="MoMo checkout is unavailable."
    )


@router.get("/plans")
async def list_momo_plans(
    user_id: str = Depends(get_current_user_id),
    token: dict = Depends(verify_firebase_token),
):
    buyer = _app_buyer(user_id, token)
    return {"plans": await get_momo_pass_service().plans_for(buyer)}


@router.post("/checkouts", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_momo_checkout(
    request: Request,
    payload: MomoCheckoutRequest,
    user_id: str = Depends(get_current_user_id),
    token: dict = Depends(verify_firebase_token),
):
    buyer = _app_buyer(user_id, token)
    try:
        return await get_momo_pass_service().start_checkout(
            buyer,
            get_momo_pass_plan(payload.plan.value),
            lang=payload.lang,
            method=payload.method,
        )
    except (MomoNotConfigured, MomoGatewayError) as error:
        raise checkout_error(error) from error


@router.get("/orders/{order_id}")
@limiter.limit("60/minute")
async def get_momo_order(
    request: Request, order_id: str, user_id: str = Depends(get_current_user_id)
):
    order = await get_momo_pass_service().app_order(order_id, user_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return order


@webhook_router.post("/ipn", status_code=status.HTTP_204_NO_CONTENT)
async def momo_ipn(request: Request):
    """MoMo's server-to-server result. MoMo expects 204 once it is recorded."""
    try:
        payload = await request.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid body"
        )
    if not await get_momo_pass_service().handle_ipn(payload):
        logger.warning("Rejected MoMo IPN for order %s", payload.get("orderId"))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature"
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
