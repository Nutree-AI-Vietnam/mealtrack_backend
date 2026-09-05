"""Durable catalog-backed meal recommendation endpoints."""

from __future__ import annotations

import logging
from datetime import datetime
from time import perf_counter

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.api.base_dependencies import (
    get_meal_recommendation_analytics_service,
    get_text_translation_service,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.meal_recommendation_route_support import (
    LogRecommendedMealRequest,
    SkipMealRecommendationSlotRequest,
    SwapMealRecommendationSlotRequest,
    capture_plan_events,
    capture_slot_event,
    record_operation_latency,
    to_slot_detail_response,
    to_summary_response,
)
from src.api.schemas.response.meal_recommendation_responses import (
    MealRecommendationPlanSummaryResponse,
    MealRecommendationSlotDetailResponse,
)
from src.app.commands.meal_recommendation import (
    CreateThreeDayMealRecommendationCommand,
    LogRecommendedMealCommand,
    SkipMealRecommendationSlotCommand,
    SwapMealRecommendationSlotCommand,
)
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.app.queries.meal_recommendation import (
    GetMealRecommendationPlanQuery,
    GetMealRecommendationSlotDetailQuery,
)
from src.app.queries.user import GetUserTimezoneQuery
from src.app.services.catalog_meal_response_localizer import (
    localize_meal_recommendation_plan,
    localize_meal_recommendation_slot,
)
from src.app.services.meal_recommendation_analytics_service import (
    MealRecommendationAnalyticsService,
)
from src.domain.exceptions.meal_recommendation_exceptions import (
    MealRecommendationCreationError,
)
from src.domain.utils.timezone_utils import get_zone_info

router = APIRouter(prefix="/v1/meal-recommendations", tags=["Meal Recommendations"])
logger = logging.getLogger(__name__)


@router.post("/three-day", response_model=MealRecommendationPlanSummaryResponse)
@limiter.limit("5/minute")
async def create_three_day_recommendations(
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    analytics_service: MealRecommendationAnalyticsService = Depends(
        get_meal_recommendation_analytics_service
    ),
    translation_service=Depends(get_text_translation_service),
) -> MealRecommendationPlanSummaryResponse:
    """Create or replay a durable three-day catalog recommendation plan."""

    started = perf_counter()
    metric_status = "error"
    normalized_idempotency_key = idempotency_key.strip()
    try:
        if not normalized_idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Idempotency-Key is required",
            )
        if len(normalized_idempotency_key) > 160:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Idempotency-Key must be 160 characters or fewer",
            )
        header_timezone = request.headers.get("X-Timezone")
        timezone = get_zone_info(
            await event_bus.send(
                GetUserTimezoneQuery(user_id=user_id, header_timezone=header_timezone)
            )
        ).key
        start_date = datetime.now(get_zone_info(timezone)).date()
        weekly_budget = await event_bus.send(
            GetWeeklyBudgetQuery(
                user_id=user_id,
                target_date=start_date,
                header_timezone=timezone,
            )
        )
        daily_calories = int(round(weekly_budget.get("adjusted_daily_calories") or 0))
        if daily_calories <= 0:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Meal recommendation target is unavailable",
            )
        try:
            plan = await event_bus.send(
                CreateThreeDayMealRecommendationCommand(
                    user_id=user_id,
                    idempotency_key=normalized_idempotency_key,
                    start_date=start_date,
                    timezone=timezone,
                    daily_calories=daily_calories,
                )
            )
        except MealRecommendationCreationError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.public_detail,
            ) from exc
        response = to_summary_response(
            await localize_meal_recommendation_plan(
                plan,
                language=get_request_language(request),
                translation_service=translation_service,
            )
        )
        await capture_plan_events(
            analytics_service,
            user_id=user_id,
            plan=plan,
            events=("plan_shown", "alternatives_shown"),
        )
        metric_status = "success"
        return response
    except HTTPException as exc:
        metric_status = f"http_{exc.status_code}"
        raise
    finally:
        record_operation_latency("create", started, metric_status)


@router.post(
    "/{plan_id}/slots/{slot_id}/swap",
    response_model=MealRecommendationSlotDetailResponse,
)
@limiter.limit("20/minute")
async def swap_meal_recommendation_slot(
    request: Request,
    plan_id: str,
    slot_id: str,
    body: SwapMealRecommendationSlotRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    analytics_service: MealRecommendationAnalyticsService = Depends(
        get_meal_recommendation_analytics_service
    ),
    translation_service=Depends(get_text_translation_service),
) -> MealRecommendationSlotDetailResponse:
    started = perf_counter()
    metric_status = "error"
    try:
        result = await event_bus.send(
            SwapMealRecommendationSlotCommand(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                request_id=body.request_id,
                expected_selection_version=body.expected_selection_version,
                alternative_catalog_meal_id=body.alternative_catalog_meal_id,
                reason=body.reason,
            )
        )
    except MealRecommendationCreationError as exc:
        metric_status = f"http_{exc.status_code}"
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail) from exc
    response = to_slot_detail_response(
        result.plan_id,
        await localize_meal_recommendation_slot(
            result.slot,
            language=get_request_language(request),
            translation_service=translation_service,
        ),
    )
    await _capture_mutation_slot_event(
        analytics_service=analytics_service,
        user_id=user_id,
        event="swap_selected",
        plan_id=result.plan_id,
    )
    metric_status = "success"
    record_operation_latency(
        "swap", started, metric_status, outcome=result.outcome
    )
    return response


@router.post(
    "/{plan_id}/slots/{slot_id}/log",
    response_model=MealRecommendationSlotDetailResponse,
)
@limiter.limit("20/minute")
async def log_recommended_meal(
    request: Request,
    plan_id: str,
    slot_id: str,
    body: LogRecommendedMealRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    analytics_service: MealRecommendationAnalyticsService = Depends(
        get_meal_recommendation_analytics_service
    ),
    translation_service=Depends(get_text_translation_service),
) -> MealRecommendationSlotDetailResponse:
    started = perf_counter()
    metric_status = "error"
    try:
        result = await event_bus.send(
            LogRecommendedMealCommand(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                request_id=body.request_id,
                language=get_request_language(request),
            )
        )
    except MealRecommendationCreationError as exc:
        metric_status = f"http_{exc.status_code}"
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail) from exc
    response = to_slot_detail_response(
        result.plan_id,
        await localize_meal_recommendation_slot(
            result.slot,
            language=get_request_language(request),
            translation_service=translation_service,
        ),
    )
    await _capture_mutation_slot_event(
        analytics_service=analytics_service,
        user_id=user_id,
        event="meal_logged",
        plan_id=result.plan_id,
    )
    metric_status = "success"
    record_operation_latency("log", started, metric_status)
    return response


@router.post(
    "/{plan_id}/slots/{slot_id}/skip",
    response_model=MealRecommendationSlotDetailResponse,
)
@limiter.limit("20/minute")
async def skip_meal_recommendation_slot(
    request: Request,
    plan_id: str,
    slot_id: str,
    body: SkipMealRecommendationSlotRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    analytics_service: MealRecommendationAnalyticsService = Depends(
        get_meal_recommendation_analytics_service
    ),
    translation_service=Depends(get_text_translation_service),
) -> MealRecommendationSlotDetailResponse:
    started = perf_counter()
    metric_status = "error"
    try:
        result = await event_bus.send(
            SkipMealRecommendationSlotCommand(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                request_id=body.request_id,
            )
        )
    except MealRecommendationCreationError as exc:
        metric_status = f"http_{exc.status_code}"
        raise HTTPException(status_code=exc.status_code, detail=exc.public_detail) from exc
    response = to_slot_detail_response(
        result.plan_id,
        await localize_meal_recommendation_slot(
            result.slot,
            language=get_request_language(request),
            translation_service=translation_service,
        ),
    )
    await _capture_mutation_slot_event(
        analytics_service=analytics_service,
        user_id=user_id,
        event="meal_skipped",
        plan_id=result.plan_id,
    )
    metric_status = "success"
    record_operation_latency("skip", started, metric_status)
    return response


@router.get("/{plan_id}", response_model=MealRecommendationPlanSummaryResponse)
async def get_meal_recommendation_plan(
    plan_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    analytics_service: MealRecommendationAnalyticsService = Depends(
        get_meal_recommendation_analytics_service
    ),
    translation_service=Depends(get_text_translation_service),
) -> MealRecommendationPlanSummaryResponse:
    """Read an owner-scoped durable recommendation plan."""

    started = perf_counter()
    metric_status = "error"
    plan = await event_bus.send(
        GetMealRecommendationPlanQuery(user_id=user_id, plan_id=plan_id)
    )
    if plan is None:
        metric_status = "http_404"
        record_operation_latency("read", started, metric_status)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    response = to_summary_response(
        await localize_meal_recommendation_plan(
            plan,
            language=get_request_language(request),
            translation_service=translation_service,
        )
    )
    await capture_plan_events(
        analytics_service,
        user_id=user_id,
        plan=plan,
        events=("plan_shown", "alternatives_shown"),
    )
    metric_status = "success"
    record_operation_latency("read", started, metric_status)
    return response


@router.get(
    "/{plan_id}/slots/{slot_id}",
    response_model=MealRecommendationSlotDetailResponse,
)
async def get_meal_recommendation_slot_detail(
    plan_id: str,
    slot_id: str,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    analytics_service: MealRecommendationAnalyticsService = Depends(
        get_meal_recommendation_analytics_service
    ),
    translation_service=Depends(get_text_translation_service),
) -> MealRecommendationSlotDetailResponse:
    """Read one owner-scoped recommendation slot with alternatives."""

    started = perf_counter()
    metric_status = "error"
    slot = await event_bus.send(
        GetMealRecommendationSlotDetailQuery(
            user_id=user_id,
            plan_id=plan_id,
            slot_id=slot_id,
        )
    )
    if slot is None:
        metric_status = "http_404"
        record_operation_latency("slot_detail", started, metric_status)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    response = to_slot_detail_response(
        plan_id,
        await localize_meal_recommendation_slot(
            slot,
            language=get_request_language(request),
            translation_service=translation_service,
        ),
    )
    await capture_slot_event(
        analytics_service,
        user_id=user_id,
        event="slot_detail_shown",
        plan_id=plan_id,
    )
    metric_status = "success"
    record_operation_latency("slot_detail", started, metric_status)
    return response


async def _capture_mutation_slot_event(
    *,
    analytics_service: MealRecommendationAnalyticsService,
    user_id: str,
    event: str,
    plan_id: str,
) -> None:
    try:
        await capture_slot_event(
            analytics_service,
            user_id=user_id,
            event=event,
            plan_id=plan_id,
        )
    except Exception:
        logger.warning(
            "meal recommendation mutation analytics failed event=%s",
            event,
            exc_info=True,
        )
