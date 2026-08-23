"""Meal edit routes — ingredients, photos, delete."""

import hashlib
import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, Request

from src.api.base_dependencies import (
    get_ai_model_manager,
    get_async_food_reference_repository,
    get_cache_service,
    get_meal_translation_service,
)
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.dependencies.task_manager import get_optional_task_manager
from src.api.exceptions import ValidationException
from src.api.mappers.meal_locale_ensure import ensure_requested_meal_translation
from src.api.mappers.meal_mapper import MealMapper
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.meals_route_helpers import (
    load_food_reference_display_projections,
)
from src.api.schemas.request.meal_requests import (
    AttachMealPhotoRequest,
    EditMealIngredientsRequest,
)
from src.app.commands.meal import (
    CustomNutritionData,
    EditMealCommand,
    FoodItemChange,
    NutritionOverride,
)
from src.app.commands.meal.attach_meal_photo_command import AttachMealPhotoCommand
from src.app.commands.meal.delete_meal_command import DeleteMealCommand
from src.app.commands.meal.delete_meal_photo_command import DeleteMealPhotoCommand
from src.app.queries.meal import GetMealByIdQuery
from src.app.services.meal_value_insight_scheduler import (
    schedule_value_insight_generation,
)
from src.domain.ports.cache_port import CachePort
from src.domain.ports.meal_insight_ai_port import MealInsightAIPort
from src.infra.event_bus import BackgroundTaskManager, EventBus

logger = logging.getLogger(__name__)
router = APIRouter()

# Ingredient PUTs are cheap writes. 60/minute is a backstop, not an AI cap.
MEAL_INGREDIENTS_EDIT_LIMIT = "60/minute"


def _validate_uploaded_meal_photo_url(image_url: str, image_id: str) -> None:
    parsed = urlparse(image_url)
    if parsed.scheme != "https" or parsed.netloc != "res.cloudinary.com":
        raise ValidationException("image_url must be a Cloudinary secure URL")
    if image_id not in parsed.path:
        raise ValidationException("image_id does not match image_url")


@router.delete("/{meal_id}")
async def delete_meal(
    meal_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """Hard-delete a meal (idempotent — returns success if already deleted)."""
    command = DeleteMealCommand(meal_id=meal_id, user_id=user_id)
    result = await event_bus.send(command)
    return result


@router.put("/{meal_id}/ingredients", response_model=None)
@limiter.limit(MEAL_INGREDIENTS_EDIT_LIMIT)
async def update_meal_ingredients(
    meal_id: str,
    payload: EditMealIngredientsRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
    cache_service: CachePort | None = Depends(get_cache_service),
    task_manager: BackgroundTaskManager | None = Depends(get_optional_task_manager),
    ai_manager: MealInsightAIPort = Depends(get_ai_model_manager),
    x_nutrition_contract_version: int | None = Header(
        default=None, alias="X-Nutrition-Contract-Version"
    ),
    x_app_version: str | None = Header(default=None, alias="X-App-Version"),
    x_platform: str | None = Header(default=None, alias="X-Platform"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    meal_translation_service=Depends(get_meal_translation_service),
    food_reference_repository=Depends(get_async_food_reference_repository),
):
    """
    Update meal ingredients and portions.

    Supports adding, removing, and modifying ingredients with automatic nutrition recalculation.
    Requires authentication - users can only modify their own meals.
    """
    logger.info("Updating meal ingredients for meal %s", meal_id)
    _validate_edit_contract_headers(
        payload.nutrition_contract_version,
        x_nutrition_contract_version,
        x_app_version,
        x_platform,
    )
    if payload.nutrition_contract_version == 2 and (
        not idempotency_key or len(idempotency_key) > 255
    ):
        raise ValidationException(
            message="Idempotency-Key must be present and at most 255 characters",
            error_code="IDEMPOTENCY_KEY_INVALID",
        )
    food_item_changes = []
    for change_request in payload.food_item_changes:
        custom_nutrition = None
        if change_request.custom_nutrition:
            custom_nutrition = CustomNutritionData(
                calories_per_100g=change_request.custom_nutrition.calories_per_100g,
                protein_per_100g=change_request.custom_nutrition.protein_per_100g,
                carbs_per_100g=change_request.custom_nutrition.carbs_per_100g,
                fat_per_100g=change_request.custom_nutrition.fat_per_100g,
                fiber_per_100g=change_request.custom_nutrition.fiber_per_100g,
                sugar_per_100g=change_request.custom_nutrition.sugar_per_100g,
            )

        food_item_changes.append(
            FoodItemChange(
                action=change_request.action,
                id=change_request.id,
                fdc_id=change_request.fdc_id,
                name=change_request.name,
                quantity=change_request.quantity,
                unit=change_request.unit,
                custom_nutrition=custom_nutrition,
                nutrition_override=(
                    NutritionOverride(
                        calories=change_request.nutrition_override.calories,
                        protein=change_request.nutrition_override.protein,
                        carbs=change_request.nutrition_override.carbs,
                        fat=change_request.nutrition_override.fat,
                    )
                    if change_request.nutrition_override
                    else None
                ),
                clear_nutrition_override=change_request.clear_nutrition_override,
                origin=change_request.origin,
                food_reference_id=change_request.food_reference_id,
                source_namespace=change_request.source_namespace,
                source_food_id=change_request.source_food_id,
                override_intent=change_request.override_intent,
            )
        )

    command = EditMealCommand(
        meal_id=meal_id,
        user_id=user_id,
        dish_name=payload.dish_name,
        created_at=payload.created_at,
        meal_type=payload.meal_type,
        food_item_changes=food_item_changes,
        nutrition_override=(
            NutritionOverride(
                calories=payload.nutrition_override.calories,
                protein=payload.nutrition_override.protein,
                carbs=payload.nutrition_override.carbs,
                fat=payload.nutrition_override.fat,
            )
            if payload.nutrition_override
            else None
        ),
        nutrition_contract_version=payload.nutrition_contract_version,
        override_intent=payload.override_intent,
        idempotency_key=idempotency_key,
        request_fingerprint=_edit_request_fingerprint(meal_id, payload),
    )

    logger.info(
        "Dispatching meal edit",
        extra={
            "nutrition_contract_version": payload.nutrition_contract_version or 1,
            "change_count": len(payload.food_item_changes),
            "has_idempotency_key": bool(idempotency_key),
        },
    )
    result = await event_bus.send(command)
    language = get_request_language(request)
    query = GetMealByIdQuery(meal_id=meal_id, user_id=user_id)
    meal = await event_bus.send(query)
    meal = await ensure_requested_meal_translation(
        meal=meal,
        language=language,
        query=query,
        event_bus=event_bus,
        meal_translation_service=meal_translation_service,
    )
    schedule_value_insight_generation(
        task_manager,
        meal,
        language=language,
        cache_service=cache_service,
        ai_manager=ai_manager,
        event_bus=event_bus,
        user_id=user_id,
    )
    if payload.nutrition_contract_version == 2:
        result = dict(result)
        image_url = getattr(getattr(meal, "image", None), "url", None)
        display_projections = await load_food_reference_display_projections(
            meal, food_reference_repository, language=language
        )
        result["meal_detail"] = MealMapper.to_detailed_response(
            meal,
            image_url,
            target_language=language,
            display_name_by_food_reference=display_projections,
        )
    return result


def _validate_edit_contract_headers(
    body_version: int | None,
    header_version: int | None,
    app_version: str | None,
    platform: str | None,
) -> None:
    if body_version is None:
        if any(value is not None for value in (header_version, app_version, platform)):
            raise ValidationException(
                message="Nutrition contract headers require a versioned body",
                error_code="NUTRITION_CONTRACT_VERSION_MISMATCH",
            )
        return
    if body_version != 2 or header_version != body_version:
        raise ValidationException(
            message="Nutrition contract header and body version must match",
            error_code="NUTRITION_CONTRACT_VERSION_MISMATCH",
        )
    if not app_version or not platform:
        raise ValidationException(
            message="X-App-Version and X-Platform are required for v2",
            error_code="NUTRITION_CONTRACT_HEADERS_REQUIRED",
        )


def _edit_request_fingerprint(meal_id: str, payload: EditMealIngredientsRequest) -> str:
    canonical = json.dumps(
        {
            "meal_id": meal_id,
            "payload": payload.model_dump(mode="json", exclude_none=True),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


@router.put("/{meal_id}/photo", response_model=None)
async def attach_meal_photo(
    meal_id: str,
    payload: AttachMealPhotoRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Attach an already-uploaded meal photo to an existing meal.

    Requires authentication - users can only modify their own meals.
    """
    _validate_uploaded_meal_photo_url(payload.image_url, payload.image_id)
    command = AttachMealPhotoCommand(
        meal_id=meal_id,
        user_id=user_id,
        image_id=payload.image_id,
        image_url=payload.image_url,
        image_format=payload.image_format,
        size_bytes=payload.size_bytes,
    )
    return await event_bus.send(command)


@router.delete("/{meal_id}/photo", response_model=None)
async def delete_meal_photo(
    meal_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Detach the saved meal photo from an existing meal.

    Requires authentication - users can only modify their own meals.
    """
    command = DeleteMealPhotoCommand(meal_id=meal_id, user_id=user_id)
    return await event_bus.send(command)
