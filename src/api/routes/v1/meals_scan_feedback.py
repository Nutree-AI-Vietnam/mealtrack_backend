"""Opt-in feedback uses server snapshots, not client-supplied training labels."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from src.api.base_dependencies import get_scan_feedback_service
from src.api.dependencies.auth import get_current_user_id
from src.api.middleware.rate_limit import limiter
from src.app.services.scan_feedback_service import ScanFeedbackService

router = APIRouter()


class ScanFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: StrictBool
    expected_edit_count: int = Field(ge=0)
    correction_kind: Literal[
        "confirmation", "identity", "portion", "nutrition", "mixed"
    ]


@router.post("/{meal_id}/scan-feedback")
@limiter.limit("20/minute")
async def submit_scan_feedback(
    meal_id: UUID,
    payload: ScanFeedbackRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    service: ScanFeedbackService = Depends(get_scan_feedback_service),
):
    return await service.submit(
        meal_id=str(meal_id), user_id=user_id, **payload.model_dump()
    )


@router.delete("/{meal_id}/scan-feedback")
@limiter.limit("20/minute")
async def withdraw_scan_feedback(
    meal_id: UUID,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    service: ScanFeedbackService = Depends(get_scan_feedback_service),
):
    return await service.withdraw(meal_id=str(meal_id), user_id=user_id)
