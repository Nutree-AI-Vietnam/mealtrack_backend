"""Weekly recap ("Weekly Wrapped") API endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.app.queries.progress import GetLatestWeeklyRecapQuery

router = APIRouter(prefix="/v1/recaps", tags=["Recaps"])


@router.get(
    "/latest",
    responses={204: {"description": "The latest week has nothing to recap."}},
)
async def get_latest_weekly_recap(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
):
    """The recap of the newest finished week, out Monday 08:00 local time."""
    query = GetLatestWeeklyRecapQuery(
        user_id=user_id,
        header_timezone=request.headers.get("X-Timezone"),
        locale=request.headers.get("Accept-Language") or "en",
    )
    payload = await event_bus.send(query)
    if payload is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return payload
