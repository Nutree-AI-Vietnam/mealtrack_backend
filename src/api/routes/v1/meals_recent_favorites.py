"""Recent and favorite meals API routes."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, status

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.mappers.meal_mapper import MealMapper
from src.api.middleware.accept_language import get_request_language
from src.api.schemas.request.meal_requests import RepeatMealRequest
from src.api.schemas.response import (
    DetailedMealResponse,
    FavoriteMealActionResponse,
    FavoriteMealsListResponse,
    RecentMealsListResponse,
)
from src.app.commands.meal import (
    FavoriteMealCommand,
    RepeatMealCommand,
    UnfavoriteMealCommand,
)
from src.app.queries.meal import (
    GetFavoriteMealsQuery,
    GetRecentMealsQuery,
)
from src.app.queries.user import GetUserTimezoneQuery

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/recent", response_model=RecentMealsListResponse)
async def list_recent_meals(
    request: Request,
    limit: int = Query(10, ge=1, le=10, description="Max recent meals to return"),
    user_id: str = Depends(get_current_user_id),
    language: str = Depends(get_request_language),
    event_bus: Any = Depends(get_configured_event_bus),
) -> RecentMealsListResponse:

    """Retrieve up to 10 distinct recent meals from the last 7 local calendar days."""
    header_tz = request.headers.get("X-Timezone")
    user_tz = await event_bus.send(
        GetUserTimezoneQuery(user_id=user_id, header_timezone=header_tz)
    )
    query = GetRecentMealsQuery(
        user_id=user_id,
        user_timezone=user_tz,
        limit=limit,
        language=language,
    )
    result = await event_bus.send(query)
    return RecentMealsListResponse(**result)


@router.get("/favorites", response_model=FavoriteMealsListResponse)
async def list_favorite_meals(
    limit: int = Query(20, ge=1, le=20, description="Max favorites to return"),
    user_id: str = Depends(get_current_user_id),
    language: str = Depends(get_request_language),
    event_bus: Any = Depends(get_configured_event_bus),
) -> FavoriteMealsListResponse:
    """Retrieve user's favorite meals ordered newest-favorited first."""
    query = GetFavoriteMealsQuery(
        user_id=user_id,
        limit=limit,
        language=language,
    )
    result = await event_bus.send(query)
    return FavoriteMealsListResponse(**result)


@router.put("/{meal_id}/favorite", response_model=FavoriteMealActionResponse)
async def favorite_meal(
    meal_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
) -> FavoriteMealActionResponse:
    """Add an active food meal to the authenticated user's favorites."""
    command = FavoriteMealCommand(
        user_id=user_id,
        meal_id=meal_id,
    )
    result = await event_bus.send(command)
    return FavoriteMealActionResponse(**result)


@router.delete("/{meal_id}/favorite", response_model=FavoriteMealActionResponse)
async def unfavorite_meal(
    meal_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: Any = Depends(get_configured_event_bus),
) -> FavoriteMealActionResponse:
    """Remove a meal from the authenticated user's favorites."""
    command = UnfavoriteMealCommand(
        user_id=user_id,
        meal_id=meal_id,
    )
    result = await event_bus.send(command)
    return FavoriteMealActionResponse(**result)


@router.post(
    "/{meal_id}/repeat",
    response_model=DetailedMealResponse,
    status_code=status.HTTP_201_CREATED,
)
async def repeat_meal(
    meal_id: str,
    payload: RepeatMealRequest | None = None,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user_id),
    language: str = Depends(get_request_language),
    event_bus: Any = Depends(get_configured_event_bus),
) -> DetailedMealResponse:
    """Repeat an active or favorited meal with durable idempotency."""
    key = idempotency_key or str(uuid.uuid4())
    meal_type = payload.meal_type if payload else None
    command = RepeatMealCommand(
        user_id=user_id,
        meal_id=meal_id,
        idempotency_key=key,
        meal_type=meal_type,
        language=language,
    )
    new_meal = await event_bus.send(command)
    detailed = MealMapper.to_detailed_response(
        new_meal,
        target_language=language,
    )
    return detailed
