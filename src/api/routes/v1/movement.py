"""Movement API endpoints."""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from src.api.base_dependencies import (
    get_describe_log_guard,
    get_read_only_uow_factory,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.movement_requests import (
    DescribeMovementRequest,
    LogMovementRequest,
    UpdateMovementRequest,
)
from src.api.schemas.response.describe_log_responses import DescribeMovementResponse
from src.api.services.describe_log_requests import run_describe
from src.app.commands.movement import (
    DeleteMovementEntryCommand,
    DescribeMovementCommand,
    LogMovementCommand,
    UpdateMovementEntryCommand,
)
from src.app.queries.movement import GetDailyMovementQuery, GetMovementCatalogQuery
from src.app.services.describe_log_guard import DescribeLogGuard
from src.app.services.describe_log_replay import (
    MOVEMENT_DESCRIBE_OPERATION,
    DescribeKey,
)
from src.infra.event_bus import EventBus

router = APIRouter(prefix="/v1/movement", tags=["Movement"])


def _parse_date(value: Optional[str]):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationException("Invalid date format. Use YYYY-MM-DD", "INVALID_DATE") from exc


@router.get("/catalog")
async def get_movement_catalog(
    _: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    return await event_bus.send(GetMovementCatalogQuery())


@router.post("/log", status_code=status.HTTP_201_CREATED)
async def log_movement(
    body: LogMovementRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    command = LogMovementCommand(
        user_id=user_id,
        activity_id=body.activity_id,
        activity_name=body.activity_name,
        duration_min=body.duration_min,
        kcal_burned=body.kcal_burned,
        intensity=body.intensity,
        include_in_balance=body.include_in_balance,
        target_date=_parse_date(body.target_date),
        header_timezone=request.headers.get("X-Timezone"),
    )
    return await event_bus.send(command)


@router.post("/describe", response_model=DescribeMovementResponse)
@limiter.limit("20/minute")
async def describe_movement(
    request: Request,
    body: DescribeMovementRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
    guard: DescribeLogGuard = Depends(get_describe_log_guard),
    replay_uow_factory: Any = Depends(get_read_only_uow_factory),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> DescribeMovementResponse:
    """Log every activity in one sentence (Ghi nhanh, Premium)."""
    target_date = _parse_date(body.target_date)
    header_timezone = request.headers.get("X-Timezone")

    async def send(sentence: str, key: DescribeKey) -> dict:
        return await event_bus.send(
            DescribeMovementCommand(
                user_id=user_id,
                text=sentence,
                target_date=target_date,
                weight_kg=body.weight_kg,
                header_timezone=header_timezone,
                idempotency_key=key.idempotency_key,
                request_fingerprint=key.request_fingerprint,
            )
        )

    # The weight is left out: a resend priced with a newer weight is still
    # the same sentence and replays the stored reply.
    return await run_describe(
        user_id=user_id,
        text=body.text,
        timezone=header_timezone,
        idempotency_key=idempotency_key,
        operation=MOVEMENT_DESCRIBE_OPERATION,
        fingerprint={"target_date": body.target_date},
        guard=guard,
        replay_uow_factory=replay_uow_factory,
        send=send,
        response_model=DescribeMovementResponse,
    )


@router.get("/daily")
async def get_daily_movement(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    date: Optional[str] = Query(None),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    query = GetDailyMovementQuery(
        user_id=user_id,
        target_date=_parse_date(date),
        header_timezone=request.headers.get("X-Timezone"),
    )
    return await event_bus.send(query)


@router.patch("/{entry_id}")
async def update_movement_entry(
    entry_id: str,
    body: UpdateMovementRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    command = UpdateMovementEntryCommand(
        user_id=user_id,
        entry_id=entry_id,
        duration_min=body.duration_min,
        kcal_burned=body.kcal_burned,
        intensity=body.intensity,
        include_in_balance=body.include_in_balance,
    )
    return await event_bus.send(command)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_movement_entry(
    entry_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    await event_bus.send(DeleteMovementEntryCommand(user_id=user_id, entry_id=entry_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
