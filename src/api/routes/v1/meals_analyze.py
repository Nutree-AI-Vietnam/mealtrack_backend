"""POST /v1/meals/image/analyze — meal photo analysis."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Request,
    UploadFile,
    status,
)

from src.api.base_dependencies import (
    get_ai_model_manager,
    get_async_food_reference_repository,
    get_cache_service,
    get_image_store,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.dependencies.task_manager import get_optional_task_manager
from src.api.exceptions import ValidationException, handle_exception
from src.api.mappers.meal_mapper import MealMapper
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.meals_route_helpers import (
    load_food_reference_display_projections,
    parse_target_date,
)
from src.api.schemas.response import DetailedMealResponse
from src.app.commands.meal.upload_meal_image_immediately_command import (
    UploadMealImageImmediatelyCommand,
)
from src.app.services.meal_value_insight_scheduler import (
    schedule_value_insight_generation,
)
from src.domain.exceptions.ai_exceptions import MealResponseLocalizationError
from src.domain.ports.cache_port import CachePort
from src.domain.ports.meal_insight_ai_port import MealInsightAIPort
from src.domain.services.prompts.input_sanitizer import sanitize_user_description
from src.infra.event_bus import BackgroundTaskManager, EventBus

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_CONTENT_TYPES = ["image/jpeg", "image/png", "image/jpg"]


async def _analyze_uploaded_image(
    *,
    request: Request,
    file: UploadFile,
    user_id: str,
    target_date: str | None,
    user_description: str | None,
    scan_mode: str,
    event_bus: EventBus,
    image_store,
    cache_service: CachePort | None,
    task_manager: BackgroundTaskManager | None,
    ai_manager: MealInsightAIPort,
    food_reference_repository=None,
) -> DetailedMealResponse:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationException(
            message=f"Invalid file type. Only {', '.join(ALLOWED_CONTENT_TYPES)} are allowed.",
            error_code="INVALID_FILE_TYPE",
            details={
                "content_type": file.content_type,
                "allowed": ALLOWED_CONTENT_TYPES,
            },
        )

    contents = await file.read()

    if len(contents) > MAX_FILE_SIZE:
        raise ValidationException(
            message=f"File size exceeds maximum allowed ({MAX_FILE_SIZE // (1024 * 1024)} MB)",
            error_code="FILE_SIZE_EXCEEDS_MAXIMUM",
            details={"size": len(contents), "max_size": MAX_FILE_SIZE},
        )

    parsed_target_date = parse_target_date(target_date)
    sanitized_description = (
        sanitize_user_description(user_description) if user_description else None
    )
    language = get_request_language(request)

    command = UploadMealImageImmediatelyCommand(
        user_id=user_id,
        file_contents=contents,
        content_type=file.content_type,
        target_date=parsed_target_date,
        user_description=sanitized_description,
        language=language,
        scan_mode=scan_mode,
    )

    try:
        meal = await event_bus.send(command)
    except MealResponseLocalizationError as e:
        raise handle_exception(e) from e
    except (RuntimeError, ValueError) as e:
        error_msg = str(e)
        logger.warning("Meal image analysis failed: %s", error_msg)
        raise ValidationException(
            message="Could not identify food in the image. Please try again with a food photo.",
            error_code="NOT_FOOD_IMAGE",
            details={"error_message": error_msg},
        ) from e

    if meal.status.value == "FAILED":
        error_message = meal.error_message or "Analysis failed"
        raise ValidationException(
            message=f"Failed to analyze meal image: {error_message}",
            error_code="FAILED_TO_ANALYZE_MEAL_IMAGE",
            details={"error_message": error_message},
        )

    image_url = None
    if meal.image:
        image_url = meal.image.url or image_store.get_url(meal.image.image_id)

    schedule_value_insight_generation(
        task_manager,
        meal,
        language=language,
        cache_service=cache_service,
        ai_manager=ai_manager,
        event_bus=event_bus,
        user_id=user_id,
    )

    display_projections = await load_food_reference_display_projections(
        meal, food_reference_repository
    )
    return MealMapper.to_detailed_response(
        meal,
        image_url,
        target_language=language,
        display_name_by_food_reference=display_projections,
    )


@router.post(
    "/image/analyze",
    status_code=status.HTTP_200_OK,
    response_model=DetailedMealResponse,
)
@limiter.limit("10/minute")
async def analyze_meal_image_immediate(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
    target_date: str | None = Query(
        None, description="Target date in YYYY-MM-DD format for meal association"
    ),
    user_description: str | None = Query(
        None,
        description="Optional user context (max 200 chars): 'no sugar', 'grilled', etc.",
    ),
    scan_mode: str = Query(
        "scanner",
        description="scanner for meal photos. Use /food-label/scan-by-url for Nutrition Facts labels.",
    ),
    event_bus: EventBus = Depends(get_configured_event_bus),
    image_store=Depends(get_image_store),
    cache_service: CachePort | None = Depends(get_cache_service),
    task_manager: BackgroundTaskManager | None = Depends(get_optional_task_manager),
    ai_manager: MealInsightAIPort = Depends(get_ai_model_manager),
    food_reference_repository=Depends(get_async_food_reference_repository),
):
    """
    Send meal photo and return immediate meal analysis with nutritional data.

    Authentication required: User ID is automatically extracted from the Firebase token.
    Language preference is read from Accept-Language header.
    """
    try:
        if scan_mode != "scanner":
            raise ValidationException(
                message=(
                    "Use /v1/meals/food-label/scan-by-url "
                    "for Nutrition Facts labels."
                ),
                error_code="INVALID_SCAN_MODE",
                details={"scan_mode": scan_mode},
            )

        return await _analyze_uploaded_image(
            request=request,
            file=file,
            user_id=user_id,
            target_date=target_date,
            user_description=user_description,
            scan_mode="scanner",
            event_bus=event_bus,
            image_store=image_store,
            cache_service=cache_service,
            task_manager=task_manager,
            ai_manager=ai_manager,
            food_reference_repository=food_reference_repository,
        )

    except Exception as e:
        raise handle_exception(e) from e
