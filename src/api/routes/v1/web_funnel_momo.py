"""Quiz-funnel MoMo one-time pass checkout, called only by the quiz BFF."""

from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.momo_payments import checkout_error
from src.api.routes.v1.web_funnel import _require_access_key, _require_bff_request
from src.bootstrap.momo_payments import (
    MomoGatewayError,
    MomoLeadUnavailable,
    MomoNotConfigured,
    get_momo_pass_service,
)
from src.domain.model.payments.momo_pass import (
    MomoPayMethod,
    MomoPlanKey,
    get_momo_pass_plan,
)

router = APIRouter(prefix="/v1/web-funnel/momo", tags=["Web Funnel"])


class WebFunnelMomoCheckoutRequest(BaseModel):
    plan: MomoPlanKey
    lead_id: str
    lang: Literal["vi", "en"] = "vi"
    method: MomoPayMethod = MomoPayMethod.WALLET


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get("/plans")
@limiter.limit("30/minute")
async def list_web_momo_plans(
    request: Request,
    lead_id: str,
    access_key: str | None = Header(default=None, alias="X-Lead-Access-Key"),
    origin: str | None = Header(default=None, alias="Origin"),
    bff_credential: str | None = Header(default=None, alias="X-Web-Funnel-BFF-Token"),
):
    _require_bff_request(origin, bff_credential)
    service = get_momo_pass_service()
    try:
        buyer = await service.web_buyer(lead_id, _require_access_key(access_key))
    except MomoLeadUnavailable:
        raise _not_found() from None
    return {"plans": await service.plans_for(buyer)}


@router.post("/checkouts", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def create_web_momo_checkout(
    request: Request,
    payload: WebFunnelMomoCheckoutRequest,
    access_key: str | None = Header(default=None, alias="X-Lead-Access-Key"),
    origin: str | None = Header(default=None, alias="Origin"),
    bff_credential: str | None = Header(default=None, alias="X-Web-Funnel-BFF-Token"),
):
    _require_bff_request(origin, bff_credential)
    service = get_momo_pass_service()
    try:
        buyer = await service.web_buyer(
            payload.lead_id, _require_access_key(access_key)
        )
        return await service.start_checkout(
            buyer,
            get_momo_pass_plan(payload.plan.value),
            lang=payload.lang,
            method=payload.method,
        )
    except MomoLeadUnavailable:
        raise _not_found() from None
    except (MomoNotConfigured, MomoGatewayError) as error:
        raise checkout_error(error) from error


@router.get("/orders/{order_id}/status")
@limiter.limit("60/minute")
async def get_web_momo_order_status(
    request: Request,
    order_id: str,
    access_key: str | None = Header(default=None, alias="X-Lead-Access-Key"),
    origin: str | None = Header(default=None, alias="Origin"),
    bff_credential: str | None = Header(default=None, alias="X-Web-Funnel-BFF-Token"),
):
    _require_bff_request(origin, bff_credential)
    order = await get_momo_pass_service().web_order(
        order_id, _require_access_key(access_key)
    )
    if not order:
        raise _not_found()
    return order
