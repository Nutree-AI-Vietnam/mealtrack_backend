"""
Meal suggestion API endpoints (Phase 06).
Simplified to only include generation endpoint.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request

from src.api.base_dependencies import get_async_food_reference_repository
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.mappers.meal_mapper import MealMapper
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.routes.v1.meals_route_helpers import (
    load_food_reference_display_projections,
)
from src.api.schemas.request.meal_suggestion_requests import (
    DiscoverMealsRequest,
    GenerateRecipesRequest,
    SaveMealSuggestionRequest,
)
from src.api.schemas.response.meal_suggestion_responses import (
    DiscoveryBatchResponse,
    RecipeBatchResponse,
    SaveMealSuggestionResponse,
)
from src.app.commands.meal_suggestion import (
    DiscoverMealsCommand,
    GenerateMealRecipesCommand,
    IngredientItem,
    SaveMealSuggestionCommand,
)
from src.app.queries.meal import GetMealByIdQuery
from src.domain.model.nutrition.macros import Macros
from src.infra.event_bus import EventBus

logger = logging.getLogger(__name__)


async def _fetch_images_parallel(
    names: list[str],
    search_fn: Callable[[str], Awaitable[Any]],
    timeout: float = 3.0,
) -> list[Any]:
    """Fetch images in parallel with per-item timeout and error isolation."""

    async def safe_fetch(name: str):
        try:
            return await asyncio.wait_for(search_fn(name), timeout=timeout)
        except Exception as e:
            logger.warning("Image fetch failed for %r: %s", name, e)
            return None

    return await asyncio.gather(*[safe_fetch(n) for n in names])


router = APIRouter(prefix="/v1/meal-suggestions", tags=["Meal Suggestions"])


@router.post("/discover", response_model=DiscoveryBatchResponse)
@limiter.limit("5/minute")
async def discover_meals(
    request: Request,
    body: DiscoverMealsRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Lightweight discovery: single AI call → 6 meals with names + macros.
    No recipes/ingredients generated here — use POST /recipes for selected meals.
    Supports pagination via session_id — previously shown meals are auto-excluded.
    """
    import uuid

    language = get_request_language(request)
    portion_type = body.get_effective_portion_type()

    command = DiscoverMealsCommand(
        user_id=user_id,
        meal_type=body.meal_type,
        meal_portion_type=portion_type.value,
        ingredients=body.ingredients,
        time_available_minutes=(
            body.cooking_time_minutes.value if body.cooking_time_minutes else None
        ),
        session_id=body.session_id,
        language=language,
        cuisine_region=body.cuisine_region,
        calorie_target=body.calorie_target,
        protein_target=body.protein_target,
        carbs_target=body.carbs_target,
        fat_target=body.fat_target,
        count=body.batch_size,
    )
    session, meals = await event_bus.send(command)

    from src.api.dependencies.food_image import get_food_image_service

    image_service = get_food_image_service()
    english_names = [m["english_name"] for m in meals]
    images = await _fetch_images_parallel(
        english_names,
        image_service.search_food_image,
        timeout=3.0,
    )

    # Translate meal names if non-English
    translated_names = [m["name"] for m in meals]
    if language and language != "en":
        from src.api.base_dependencies import (
            get_suggestion_translation_service,
        )

        try:
            translation_svc = get_suggestion_translation_service()
            if translation_svc:
                translated = await translation_svc.translate_names(
                    [m["name"] for m in meals], language
                )
                if translated and len(translated) == len(meals):
                    translated_names = translated
        except Exception as exc:
            logger.warning(
                "Name translation failed, using English error_type=%s",
                type(exc).__name__,
            )

    def _as_image_fields(x):
        """Accepts CachedImage (image_url attr) or FoodImageResult (url attr)."""
        if x is None:
            return {
                "image_url": None,
                "thumbnail_url": None,
                "image_source": None,
                "photographer": None,
                "photographer_url": None,
                "unsplash_download_location": None,
                "image_confidence": 0.0,
            }
        image_url = getattr(x, "image_url", None) or getattr(x, "url", None)
        thumbnail_url = (
            getattr(x, "thumbnail_url", None) or image_url
        )  # fallback to full URL
        return {
            "image_url": image_url,
            "thumbnail_url": thumbnail_url,
            "image_source": getattr(x, "source", None),
            "photographer": getattr(x, "photographer", None),
            "photographer_url": getattr(x, "photographer_url", None),
            "unsplash_download_location": getattr(x, "download_location", None),
            "image_confidence": float(getattr(x, "confidence", 0.0) or 0.0),
        }

    # Build response
    from src.api.schemas.response.meal_suggestion_responses import (
        DiscoveryMealResponse,
        MacroEstimateResponse,
    )

    response_meals = []
    for i, m in enumerate(meals):
        img = images[i] if i < len(images) else None
        meal_id = m.get("id") or f"disc_{uuid.uuid4().hex[:12]}"
        response_meals.append(
            DiscoveryMealResponse(
                id=meal_id,
                meal_name=translated_names[i],
                english_name=m["english_name"],
                macros=MacroEstimateResponse(
                    calories=m["calories"],
                    protein=m["protein"],
                    carbs=m["carbs"],
                    fat=m["fat"],
                ),
                **_as_image_fields(img),
            )
        )

    shown_count = len(session.shown_meal_names)
    return DiscoveryBatchResponse(
        session_id=session.id,
        meals=response_meals,
        has_more=len(meals) >= 4 and shown_count < 30,
        meal_count=len(response_meals),
    )


@router.post("/recipes", response_model=RecipeBatchResponse)
@limiter.limit("5/minute")
async def generate_recipes(
    request: Request,
    body: GenerateRecipesRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
):
    """
    Generate full recipes for 1-3 selected discovery meals.
    Called after user picks meals from the discovery grid.
    """
    language = get_request_language(request)

    recipes = await event_bus.send(
        GenerateMealRecipesCommand(
            user_id=user_id,
            meal_type=body.meal_type,
            language=language,
            meal_names=body.meal_names,
            session_id=body.session_id,
            selected_meal_ids=body.selected_meal_ids,
            selected_meals=body.selected_meals,
            ingredients=body.ingredients,
            cooking_time_minutes=body.cooking_time_minutes,
            cuisine_region=body.cuisine_region,
            calorie_target=body.calorie_target,
            protein_target=body.protein_target,
            carbs_target=body.carbs_target,
            fat_target=body.fat_target,
        )
    )

    # Map to response
    from src.api.mappers.meal_suggestion_mapper import to_meal_suggestion_response

    return RecipeBatchResponse(
        recipes=[to_meal_suggestion_response(r) for r in recipes],
    )


@router.post("/save", response_model=SaveMealSuggestionResponse)
async def save_meal_suggestion(
    request: Request,
    body: SaveMealSuggestionRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus: EventBus = Depends(get_configured_event_bus),
    food_reference_repository=Depends(get_async_food_reference_repository),
):
    """
    Save a meal suggestion as a regular meal in the meals table.

    This creates a Meal entity populated with the suggestion's nutrition data
    for the specified date so that it participates in daily macros and history.
    """
    language = get_request_language(request)
    derived_calories = round(
        Macros.raw_total_calories(body.protein, body.carbs, body.fat, body.fiber)
    )
    command = SaveMealSuggestionCommand(
        user_id=user_id,
        suggestion_id=body.suggestion_id,
        name=body.name,
        meal_type=body.meal_type,
        calories=body.calories or derived_calories,
        protein=body.protein,
        carbs=body.carbs,
        fat=body.fat,
        fiber=body.fiber,
        description=body.description,
        estimated_cook_time_minutes=body.estimated_cook_time_minutes,
        ingredients=[
            IngredientItem(
                name=i.name,
                amount=i.amount,
                unit=i.unit,
                calories=i.calories,
                food_reference_id=i.food_reference_id,
                protein=i.protein,
                carbs=i.carbs,
                fat=i.fat,
                fiber=i.fiber,
            )
            for i in body.ingredients
        ],
        instructions=[
            i.model_dump() if hasattr(i, "model_dump") else i for i in body.instructions
        ],
        portion_multiplier=body.portion_multiplier,
        meal_date=body.meal_date,
        cuisine_type=body.cuisine_type,
        origin_country=body.origin_country,
        emoji=body.emoji,
        language=language,
        image_url=body.image_url,
    )

    meal_id = await event_bus.send(command)

    # Unsplash API compliance: trigger download event (fire-and-forget, managed)
    if body.unsplash_download_location:
        from src.api.dependencies.task_manager import get_task_manager
        from src.infra.adapters.unsplash_image_adapter import UnsplashImageAdapter

        get_task_manager().spawn(
            "unsplash_download",
            UnsplashImageAdapter.trigger_download(body.unsplash_download_location),
        )

    meal = await event_bus.send(GetMealByIdQuery(meal_id=meal_id, user_id=user_id))
    display_projections = await load_food_reference_display_projections(
        meal, food_reference_repository, language=language
    )

    return SaveMealSuggestionResponse(
        meal_id=meal_id,
        message="Meal suggestion saved successfully",
        meal_date=body.meal_date,
        meal_detail=MealMapper.to_detailed_response(
            meal,
            image_url=body.image_url,
            target_language=language,
            display_name_by_food_reference=display_projections,
        ),
    )
