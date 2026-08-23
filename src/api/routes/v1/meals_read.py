"""Meal read/query routes — streak, macros, budget, meal detail."""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from src.api.base_dependencies import (
    get_ai_model_manager,
    get_async_food_reference_repository,
    get_cache_service,
    get_image_store,
    get_meal_translation_service,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.dependencies.task_manager import get_optional_task_manager
from src.api.mappers.meal_locale_ensure import (
    ensure_requested_meal_translation,
    without_requested_meal_translation,
)
from src.api.mappers.meal_mapper import MealMapper
from src.api.middleware.accept_language import get_request_language
from src.api.routes.v1.meals_route_helpers import (
    load_food_reference_display_projections,
)
from src.api.schemas.progress_schemas import DailyBreakdownResponse, StreakResponse
from src.api.schemas.response import (
    DetailedMealResponse,
    MealValueInsightsStatusResponse,
)
from src.api.schemas.response.daily_nutrition_response import DailyNutritionResponse
from src.api.schemas.response.weekly_budget_response import WeeklyBudgetResponse
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.app.queries.meal import (
    GetDailyBreakdownQuery,
    GetDailyMacrosQuery,
    GetMealByIdQuery,
    GetStreakQuery,
)
from src.app.services.meal_value_insight_scheduler import (
    get_meal_insight_user_context,
    schedule_value_insight_generation,
)
from src.domain.ports.cache_port import CachePort
from src.domain.ports.meal_insight_ai_port import MealInsightAIPort
from src.domain.services.meal_value_insight_service import MealValueInsightService
from src.infra.event_bus import BackgroundTaskManager, EventBus

logger = logging.getLogger(__name__)
router = APIRouter()


_without_requested_meal_translation = without_requested_meal_translation
_ensure_requested_meal_translation = ensure_requested_meal_translation


async def _source_nutrition_by_food_reference(meal, food_reference_repository):
    """Load per-100g density for catalog-backed ingredients via the port."""
    food_items = getattr(getattr(meal, "nutrition", None), "food_items", None) or []
    food_reference_ids = {
        item.food_reference_id
        for item in food_items
        if getattr(item, "food_reference_id", None) is not None
        and not getattr(item, "source_snapshot", None)
    }
    if not food_reference_ids:
        return {}

    batch_loader = getattr(food_reference_repository, "get_nutrition_projections", None)
    if batch_loader is not None:
        return await batch_loader(list(food_reference_ids))

    source_nutrition = {}
    for food_reference_id in food_reference_ids:
        reference = await food_reference_repository.get_nutrition_projection(
            food_reference_id
        )
        if reference is not None:
            source_nutrition[food_reference_id] = reference
    return source_nutrition


@router.get("/streak", response_model=StreakResponse)
async def get_streak(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get the user's current and best logging streak.

    - current_streak: consecutive days logged up to today (streak not broken until end of day)
    - best_streak: longest consecutive run ever
    - last_logged_date: most recent date with a meal (YYYY-MM-DD), null if never logged
    """
    header_tz = request.headers.get("X-Timezone")
    query = GetStreakQuery(user_id=user_id, header_timezone=header_tz)
    result = await event_bus.send(query)
    return result


@router.get("/weekly/daily-breakdown", response_model=DailyBreakdownResponse)
async def get_daily_breakdown(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    week_start: str | None = Query(
        None,
        description="Week start date (Monday) in YYYY-MM-DD format. Defaults to current week.",
    ),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get 7-day macro breakdown (Mon–Sun) with consumed vs target per day.

    Returns an array of 7 entries, one per day, with calories/protein/carbs/fat
    consumed and base daily targets from the user's TDEE.
    """
    parsed_week_start = None
    if week_start:
        parsed_week_start = datetime.strptime(week_start, "%Y-%m-%d").date()

    header_tz = request.headers.get("X-Timezone")
    query = GetDailyBreakdownQuery(
        user_id=user_id,
        week_start=parsed_week_start,
        header_timezone=header_tz,
    )
    result = await event_bus.send(query)
    return result


@router.get("/upload-token")
async def get_upload_token(
    user_id: str = Depends(get_current_user_id),
    image_store=Depends(get_image_store),
):
    """Return a signed Cloudinary upload token for direct client-side upload."""
    image_id = str(uuid.uuid4())
    token = await image_store.generate_upload_signature_async(image_id)
    logger.info("[UPLOAD-TOKEN] user=%s image_id=%s", user_id, image_id)
    return token


@router.get("/{meal_id}", response_model=DetailedMealResponse)
async def get_meal(
    request: Request,
    meal_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
    image_store=Depends(get_image_store),
    cache_service: CachePort | None = Depends(get_cache_service),
    task_manager: BackgroundTaskManager | None = Depends(get_optional_task_manager),
    ai_manager: MealInsightAIPort = Depends(get_ai_model_manager),
    food_reference_repository=Depends(get_async_food_reference_repository),
    meal_translation_service=Depends(get_meal_translation_service),
):
    """Get detailed information about a specific meal.

    Language preference is read from Accept-Language header.
    Requires authentication - users can only access their own meals.
    """
    query = GetMealByIdQuery(meal_id=meal_id, user_id=user_id)
    meal = await event_bus.send(query)

    image_url = None
    if meal.image:
        image_url = meal.image.url or image_store.get_url(meal.image.image_id)

    language = get_request_language(request)
    meal = await ensure_requested_meal_translation(
        meal=meal,
        language=language,
        query=query,
        event_bus=event_bus,
        meal_translation_service=meal_translation_service,
    )
    user_context = await get_meal_insight_user_context(event_bus, user_id)
    insight_service = MealValueInsightService()
    value_insights = await insight_service.get_cached_ai(
        dish_name=meal.dish_name,
        nutrition=meal.nutrition,
        ingredient_names_by_id={},
        language=language,
        user_context=user_context,
        cache_service=cache_service,
    )
    if value_insights is None:
        schedule_value_insight_generation(
            task_manager,
            meal,
            language=language,
            cache_service=cache_service,
            ai_manager=ai_manager,
            event_bus=event_bus,
            user_id=user_id,
        )

    source_nutrition = await _source_nutrition_by_food_reference(
        meal, food_reference_repository
    )
    display_projections = await load_food_reference_display_projections(
        meal, food_reference_repository, language=language
    )
    return MealMapper.to_detailed_response(
        meal,
        image_url,
        target_language=language,
        value_insights=value_insights,
        source_nutrition_by_food_reference=source_nutrition,
        display_name_by_food_reference=display_projections,
    )


@router.get("/{meal_id}/value-insights", response_model=MealValueInsightsStatusResponse)
async def get_meal_value_insights(
    request: Request,
    meal_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
    cache_service: CachePort | None = Depends(get_cache_service),
    task_manager: BackgroundTaskManager | None = Depends(get_optional_task_manager),
    ai_manager: MealInsightAIPort = Depends(get_ai_model_manager),
):
    """Return current value-insight cache status for a meal."""
    meal = await event_bus.send(GetMealByIdQuery(meal_id=meal_id, user_id=user_id))
    language = get_request_language(request)
    user_context = await get_meal_insight_user_context(event_bus, user_id)
    insight_service = MealValueInsightService()
    version = insight_service.version(
        dish_name=meal.dish_name,
        nutrition=meal.nutrition,
        ingredient_names_by_id={},
        language=language,
        user_context=user_context,
    )
    if version is None or cache_service is None:
        return MealValueInsightsStatusResponse(
            status="unavailable",
            value_insights=None,
            version=version,
        )

    value_insights = await insight_service.get_cached_ai(
        dish_name=meal.dish_name,
        nutrition=meal.nutrition,
        ingredient_names_by_id={},
        language=language,
        user_context=user_context,
        cache_service=cache_service,
    )
    if value_insights is None:
        schedule_value_insight_generation(
            task_manager,
            meal,
            language=language,
            cache_service=cache_service,
            ai_manager=ai_manager,
            event_bus=event_bus,
            user_id=user_id,
        )
        return MealValueInsightsStatusResponse(
            status="generating",
            value_insights=None,
            version=version,
        )

    return MealValueInsightsStatusResponse(
        status="fresh",
        value_insights=MealMapper.to_value_insights_response(value_insights),
        version=version,
    )


@router.get("/daily/macros", response_model=DailyNutritionResponse)
async def get_daily_macros(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    date: str | None = Query(None, description="Date in YYYY-MM-DD format"),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get daily macronutrient summary for all meals with user targets from TDEE.

    Authentication required: User ID is automatically extracted from the Firebase token.
    """
    target_date = None
    if date:
        target_date = datetime.strptime(date, "%Y-%m-%d").date()

    header_tz = request.headers.get("X-Timezone")
    query = GetDailyMacrosQuery(
        user_id=user_id,
        target_date=target_date,
        header_timezone=header_tz,
    )
    result = await event_bus.send(query)

    return MealMapper.to_daily_nutrition_response(result)


@router.get("/weekly/budget", response_model=WeeklyBudgetResponse)
async def get_weekly_budget(
    request: Request,
    user_id: str = Depends(get_current_user_id),
    week_start: str | None = Query(
        None,
        description="Week start date in YYYY-MM-DD format (Monday). Defaults to current week.",
    ),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Get weekly macro budget status.

    Returns current week's budget with consumed totals and adjusted daily targets.
    """
    target_date = None
    if week_start:
        target_date = datetime.strptime(week_start, "%Y-%m-%d").date()

    header_tz = request.headers.get("X-Timezone")
    query = GetWeeklyBudgetQuery(
        user_id=user_id,
        target_date=target_date,
        header_timezone=header_tz,
    )
    result = await event_bus.send(query)
    return result
