"""Vacation API: set a range, extend the last day, or end it."""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException
from src.app.commands.vacation import (
    EndVacationCommand,
    ExtendVacationCommand,
    SetVacationCommand,
)
from src.app.queries.vacation import GetVacationQuery
from src.infra.event_bus import EventBus

router = APIRouter(prefix="/v1/vacations", tags=["Vacations"])


class SetVacationRequest(BaseModel):
    start_date: str = Field(..., description="First day YYYY-MM-DD, inclusive")
    end_date: str = Field(..., description="Last day YYYY-MM-DD, inclusive")


class ExtendVacationRequest(BaseModel):
    end_date: str = Field(..., description="New last day YYYY-MM-DD")


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationException(
            message="Invalid date format. Use YYYY-MM-DD",
            error_code="INVALID_DATE_FORMAT",
        ) from exc


@router.get("")
async def get_vacation(
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    return await event_bus.send(GetVacationQuery(user_id=user_id))


@router.put("")
async def set_vacation(
    body: SetVacationRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    command = SetVacationCommand(
        user_id=user_id,
        start_date=_parse_date(body.start_date),
        end_date=_parse_date(body.end_date),
    )
    return await event_bus.send(command)


@router.post("/extend")
async def extend_vacation(
    body: ExtendVacationRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    command = ExtendVacationCommand(
        user_id=user_id,
        end_date=_parse_date(body.end_date),
    )
    return await event_bus.send(command)


@router.post("/end")
async def end_vacation(
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    return await event_bus.send(EndVacationCommand(user_id=user_id))
