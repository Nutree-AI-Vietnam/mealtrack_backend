"""Progress API endpoints."""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.schemas.progress_schemas import (
    JourneyProgressResponse,
    ProgressSummaryResponse,
)
from src.app.queries.progress import GetJourneyProgressQuery, GetProgressSummaryQuery

router = APIRouter(prefix="/v1/progress", tags=["Progress"])


@router.get("/journey", response_model=JourneyProgressResponse)
async def get_journey_progress(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """Get the current action-based journey progress snapshot."""
    query = GetJourneyProgressQuery(
        user_id=user_id,
        header_timezone=request.headers.get("X-Timezone"),
    )
    return await event_bus.send(query)


@router.get("/summary", response_model=ProgressSummaryResponse)
async def get_progress_summary(
    request: Request,
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """Per-day macros, targets, burn, and hydration for an inclusive range."""
    if start_date is not None and end_date is not None and start_date > end_date:
        return JSONResponse(
            status_code=422,
            content={"detail": "start_date must be on or before end_date"},
        )
    query = GetProgressSummaryQuery(
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        header_timezone=request.headers.get("X-Timezone"),
    )
    return await event_bus.send(query)
