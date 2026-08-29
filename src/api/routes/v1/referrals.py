"""Referral system API routes — code lookup, validation, application, stats, and payout."""

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from src.api.dependencies.auth import get_current_user_id
from src.app.commands.referral.apply_referral_code_command import (
    ApplyReferralCodeCommand,
)
from src.app.commands.referral.request_payout_command import RequestPayoutCommand
from src.app.handlers.command_handlers.referral.request_payout_handler import (
    MIN_WITHDRAWAL,
    RequestPayoutCommandHandler,
    validate_payout_payment_details,
)
from src.app.handlers.query_handlers.referral.get_my_referral_code_handler import (
    GetMyReferralCodeQueryHandler,
)
from src.app.handlers.query_handlers.referral.get_referral_stats_handler import (
    GetReferralStatsQueryHandler,
)
from src.app.handlers.query_handlers.referral.validate_referral_code_handler import (
    ValidateReferralCodeQueryHandler,
)
from src.app.queries.referral.get_my_referral_code_query import GetMyReferralCodeQuery
from src.app.queries.referral.get_referral_stats_query import GetReferralStatsQuery
from src.app.queries.referral.validate_referral_code_query import (
    ValidateReferralCodeQuery,
)
from src.bootstrap.referral import get_apply_referral_code_handler

router = APIRouter(prefix="/v1/referrals", tags=["Referrals"])
logger = logging.getLogger(__name__)


# ── Request / Response Schemas ────────────────────────────────────────────────


class ValidateCodeRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=15)

    @field_validator("code")
    @classmethod
    def normalise_code(cls, v: str) -> str:
        return v.strip().upper()


class ValidateCodeResponse(BaseModel):
    valid: bool
    error: str | None = None
    referrer_name: str | None = None
    discount_monthly: int = 199000
    discount_annual: int = 499000
    commission_rewards: dict[str, float] = Field(default_factory=dict)


SUPPORTED_CURRENCIES = {"VND", "USD", "EUR"}


class ApplyCodeRequest(BaseModel):
    code: str = Field(..., min_length=3, max_length=15)
    discount_applied: int
    currency: str = "VND"

    @field_validator("code")
    @classmethod
    def normalise_code(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        v = v.upper()
        if v not in SUPPORTED_CURRENCIES:
            raise ValueError(
                f"Unsupported currency: {v}. Supported: {SUPPORTED_CURRENCIES}"
            )
        return v


class MyCodeResponse(BaseModel):
    code: str
    created_at: str


class ConversionDTO(BaseModel):
    referred_name: str
    status: str
    amount: int
    date: str


class StatsResponse(BaseModel):
    code: str
    wallet_balance: int
    total_earned: int
    total_withdrawn: int
    total_invited: int
    total_converted: int
    conversions: list[ConversionDTO]
    has_pending_payout: bool
    commission_rewards: dict[str, float] = Field(default_factory=dict)


class PayoutRequest(BaseModel):
    amount: int = Field(..., ge=MIN_WITHDRAWAL)
    payment_method: Literal["momo", "bank"]
    payment_details: dict[str, Any]

    @field_validator("payment_details")
    @classmethod
    def validate_payment_details(cls, value: dict[str, Any], info) -> dict[str, Any]:
        method = info.data.get("payment_method")
        if method:
            validate_payout_payment_details(method, value)
        return value


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/validate", response_model=ValidateCodeResponse)
async def validate_code(
    request: ValidateCodeRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Validate a referral code before the user completes their purchase."""
    handler = ValidateReferralCodeQueryHandler()
    result = await handler.handle(
        ValidateReferralCodeQuery(code=request.code, user_id=user_id),
    )
    return ValidateCodeResponse(**result.__dict__)


@router.post("/apply", status_code=status.HTTP_201_CREATED)
async def apply_code(
    request: ApplyCodeRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Record the referred user's code application (call after purchase confirmation)."""
    try:
        handler = get_apply_referral_code_handler()
        await handler.handle(
            ApplyReferralCodeCommand(
                user_id=user_id,
                code=request.code,
                discount_applied=request.discount_applied,
                currency=request.currency,
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"success": True}


@router.get("/my-code", response_model=MyCodeResponse)
async def get_my_code(user_id: str = Depends(get_current_user_id)):
    """Return (or lazily create) the authenticated user's personal referral code."""
    handler = GetMyReferralCodeQueryHandler()
    result = await handler.handle(GetMyReferralCodeQuery(user_id=user_id))
    return MyCodeResponse(**result.__dict__)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(user_id: str = Depends(get_current_user_id)):
    """Return wallet balance, lifetime totals, and per-conversion history."""
    handler = GetReferralStatsQueryHandler()
    result = await handler.handle(GetReferralStatsQuery(user_id=user_id))
    return StatsResponse(
        code=result.code,
        wallet_balance=result.wallet_balance,
        total_earned=result.total_earned,
        total_withdrawn=result.total_withdrawn,
        total_invited=result.total_invited,
        total_converted=result.total_converted,
        conversions=[ConversionDTO(**c.__dict__) for c in result.conversions],
        has_pending_payout=result.has_pending_payout,
        commission_rewards=result.commission_rewards,
    )


@router.post("/payout", status_code=status.HTTP_201_CREATED)
async def request_payout(
    request: PayoutRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Request a withdrawal of referral wallet balance (minimum ₫100,000)."""
    handler = RequestPayoutCommandHandler()
    try:
        await handler.handle(
            RequestPayoutCommand(
                user_id=user_id,
                amount=request.amount,
                payment_method=request.payment_method,
                payment_details=request.payment_details,
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"success": True}
