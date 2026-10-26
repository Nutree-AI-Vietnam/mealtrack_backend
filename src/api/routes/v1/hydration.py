"""Hydration API endpoints."""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Query, Request, status

from src.api.base_dependencies import (
    get_describe_log_guard,
    get_read_only_uow_factory,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.hydration_requests import (
    DescribeHydrationRequest,
    LogCaloricDrinkRequest,
    LogHydrationRequest,
)
from src.api.schemas.response.describe_log_responses import DescribeHydrationResponse
from src.api.services.describe_log_requests import run_describe
from src.app.commands.hydration import (
    DeleteHydrationEntryCommand,
    DescribeHydrationCommand,
    LogCaloricDrinkCommand,
    LogHydrationCommand,
)
from src.app.queries.hydration import (
    GetDailyHydrationQuery,
    GetDrinkCatalogQuery,
    GetWeeklyHydrationQuery,
)
from src.app.services.describe_log_guard import DescribeLogGuard
from src.app.services.describe_log_replay import (
    HYDRATION_DESCRIBE_OPERATION,
    DescribeKey,
)
from src.infra.event_bus import EventBus

router = APIRouter(prefix="/v1/hydration", tags=["Hydration"])


@router.get("/catalog", response_model=None)
async def get_drink_catalog(
    request: Request,
    _: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Get the drink catalog with hydration and caloric categories."""
    query = GetDrinkCatalogQuery(language=get_request_language(request))
    return await event_bus.send(query)


@router.post("/log", status_code=status.HTTP_201_CREATED)
async def log_hydration(
    body: LogHydrationRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Log a hydration entry for the current user."""
    target_date = None
    if body.target_date:
        try:
            target_date = datetime.strptime(body.target_date, "%Y-%m-%d").date()
        except ValueError as e:
            raise ValidationException("Invalid date format. Use YYYY-MM-DD") from e

    header_timezone = request.headers.get("X-Timezone")
    language = get_request_language(request)
    command = LogHydrationCommand(
        user_id=user_id,
        drink_id=body.drink_id,
        volume_ml=body.volume_ml,
        target_date=target_date,
        header_timezone=header_timezone,
        language=language,
    )
    return await event_bus.send(command)


@router.post("/log/drink", status_code=status.HTTP_201_CREATED)
async def log_caloric_drink(
    body: LogCaloricDrinkRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Log a caloric drink entry for the current user."""
    target_date = None
    if body.target_date:
        try:
            target_date = datetime.strptime(body.target_date, "%Y-%m-%d").date()
        except ValueError as e:
            raise ValidationException("Invalid date format. Use YYYY-MM-DD") from e

    header_timezone = request.headers.get("X-Timezone")
    language = get_request_language(request)
    command = LogCaloricDrinkCommand(
        user_id=user_id,
        drink_id=body.drink_id,
        volume_ml=body.volume_ml,
        target_date=target_date,
        header_timezone=header_timezone,
        language=language,
    )
    return await event_bus.send(command)


@router.post("/describe", response_model=DescribeHydrationResponse)
@limiter.limit("20/minute")
async def describe_hydration(
    request: Request,
    body: DescribeHydrationRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
    guard: DescribeLogGuard = Depends(get_describe_log_guard),
    replay_uow_factory: Any = Depends(get_read_only_uow_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> DescribeHydrationResponse:
    """Log every drink in one sentence (Ghi nhanh, Premium)."""
    target_date = None
    if body.target_date:
        try:
            target_date = datetime.strptime(body.target_date, "%Y-%m-%d").date()
        except ValueError as e:
            raise ValidationException("Invalid date format. Use YYYY-MM-DD") from e

    header_timezone = request.headers.get("X-Timezone")
    language = get_request_language(request)

    async def send(sentence: str, key: DescribeKey) -> dict:
        return await event_bus.send(
            DescribeHydrationCommand(
                user_id=user_id,
                text=sentence,
                target_date=target_date,
                header_timezone=header_timezone,
                language=language,
                idempotency_key=key.idempotency_key,
                request_fingerprint=key.request_fingerprint,
            )
        )

    return await run_describe(
        user_id=user_id,
        text=body.text,
        timezone=header_timezone,
        idempotency_key=idempotency_key,
        operation=HYDRATION_DESCRIBE_OPERATION,
        fingerprint={"target_date": body.target_date},
        guard=guard,
        replay_uow_factory=replay_uow_factory,
        send=send,
        response_model=DescribeHydrationResponse,
    )


@router.get("/daily", response_model=None)
async def get_daily_hydration(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    date: Optional[str] = Query(
        None, description="Date in YYYY-MM-DD format, defaults to today"
    ),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Get hydration summary for a specific date."""
    target_date = None
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError as e:
            raise ValidationException("Invalid date format. Use YYYY-MM-DD") from e

    header_timezone = request.headers.get("X-Timezone")
    language = get_request_language(request)
    query = GetDailyHydrationQuery(
        user_id=user_id,
        target_date=target_date,
        header_timezone=header_timezone,
        language=language,
    )
    return await event_bus.send(query)


@router.get("/weekly", response_model=None)
async def get_weekly_hydration(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    start_date: Optional[str] = Query(
        None,
        description="Monday of the week in YYYY-MM-DD format, defaults to current week",
    ),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Get 7-day hydration chart data."""
    parsed_start = None
    if start_date:
        try:
            parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except ValueError as e:
            raise ValidationException("Invalid date format. Use YYYY-MM-DD") from e

    header_timezone = request.headers.get("X-Timezone")
    query = GetWeeklyHydrationQuery(
        user_id=user_id,
        start_date=parsed_start,
        header_timezone=header_timezone,
    )
    return await event_bus.send(query)


@router.delete("/{entry_id}")
async def delete_hydration_entry(
    entry_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Delete a hydration entry."""
    command = DeleteHydrationEntryCommand(user_id=user_id, entry_id=entry_id)
    return await event_bus.send(command)
