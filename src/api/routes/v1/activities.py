"""
Activities API endpoints - Event-driven architecture.
"""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import ValidationException
from src.api.middleware.accept_language import get_request_language
from src.app.queries.activity import GetDailyActivitiesQuery, GetBulkActivitiesQuery
from src.domain.utils.timezone_utils import utc_now
from src.infra.event_bus import EventBus

router = APIRouter(
    prefix="/v1/activities",
    tags=["activities"],
)


@router.get("/daily", response_model=None)
async def get_daily_activities(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    date: Optional[str] = Query(
        None, description="Date in YYYY-MM-DD format, defaults to today"
    ),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get all activities (meals and workouts) for a specific date.

    Returns a unified list of activities including:
    - Meal activities with nutrition data
    - Workout activities (placeholder for future implementation)
    - Hydration entries: localized ``title`` plus optional ``drink_id`` for
      catalog matching independent of the stored display name

    Activities are sorted by timestamp in descending order (newest first).

    Authentication required: User ID is automatically extracted from the Firebase token.
    Language preference is read from Accept-Language header.
    """
    # Parse and validate date
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError as e:
            raise ValidationException("Invalid date format. Use YYYY-MM-DD") from e
    else:
        target_date = utc_now()

    # Get language from Accept-Language header
    language = get_request_language(request)

    # Send query with language and timezone support
    header_tz = request.headers.get("X-Timezone")
    query = GetDailyActivitiesQuery(
        user_id=user_id,
        target_date=target_date,
        language=language,
        header_timezone=header_tz,
    )
    activities = await event_bus.send(query)

    return activities


@router.get("/bulk", response_model=None)
async def get_bulk_activities(
    request: Request,
    start: date = Query(..., description="Start date (inclusive), YYYY-MM-DD"),
    end: date = Query(..., description="End date (inclusive), YYYY-MM-DD"),
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get all activities for a date range, grouped by date.

    Returns a dict of { "YYYY-MM-DD": [activities] } for each date in the range.
    Max range: 60 days.
    """
    if start > end:
        raise ValidationException("Start date must be before or equal to end date")
    if (end - start).days > 60:
        raise ValidationException("Date range cannot exceed 60 days")

    language = get_request_language(request)
    header_tz = request.headers.get("X-Timezone")
    query = GetBulkActivitiesQuery(
        user_id=user_id,
        start_date=start,
        end_date=end,
        language=language,
        header_timezone=header_tz,
    )
    return await event_bus.send(query)
