"""Progress API endpoints."""

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.schemas.progress_schemas import (
    JourneyProgressResponse,
    ProgressRecapResponse,
    ProgressSummaryResponse,
)
from src.app.commands.progress import GenerateProgressRecapCommand
from src.app.queries.progress import (
    GetJourneyProgressQuery,
    GetProgressRecapQuery,
    GetProgressSummaryQuery,
)
from src.domain.services.progress_recap_facts import HORIZONS

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


def _recap_error(
    start_date: date | None, end_date: date | None, horizon: str
) -> JSONResponse | None:
    if start_date is not None and end_date is not None and start_date > end_date:
        return JSONResponse(
            status_code=422,
            content={"detail": "start_date must be on or before end_date"},
        )
    if horizon not in HORIZONS:
        return JSONResponse(
            status_code=422,
            content={"detail": "horizon must be day, week, month, or year"},
        )
    return None


@router.get("/recap", response_model=ProgressRecapResponse)
async def get_progress_recap(
    request: Request,
    horizon: str = Query(...),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """Return the cached AI recap for one Progress timeline window."""
    error = _recap_error(start_date, end_date, horizon)
    if error is not None:
        return error
    query = GetProgressRecapQuery(
        user_id=user_id,
        horizon=horizon,
        start_date=start_date,
        end_date=end_date,
        header_timezone=request.headers.get("X-Timezone"),
        locale=request.headers.get("Accept-Language") or "en",
    )
    return await event_bus.send(query)


@router.post("/recap", response_model=ProgressRecapResponse)
async def generate_progress_recap(
    request: Request,
    horizon: str = Query(...),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    force: bool = Query(False),
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """Analyze the selected timeline and write a recap + highlights."""
    error = _recap_error(start_date, end_date, horizon)
    if error is not None:
        return error
    command = GenerateProgressRecapCommand(
        user_id=user_id,
        horizon=horizon,
        start_date=start_date,
        end_date=end_date,
        header_timezone=request.headers.get("X-Timezone"),
        locale=request.headers.get("Accept-Language") or "en",
        force=force,
    )
    return await event_bus.send(command)
