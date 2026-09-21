"""Weekly meal planner and recipe API routes."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from src.api.base_dependencies import get_text_translation_service
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import create_http_exception, handle_exception
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.weekly_meal_planner_requests import (
    AiAdjustMealPlanRequest,
    GenerateWeeklyMealPlanRequest,
    LogMealPlanSlotRequest,
    UpdateMealPlanPantryRequest,
    UpdateWeeklyMealPlanRequest,
)
from src.api.schemas.response.weekly_meal_planner_responses import (
    GroceryCategoryResponse,
    GroceryItemResponse,
    LogMealPlanSlotResponse,
    PantryUpdateResponse,
    RecipeDetailNutritionResponse,
    RecipeDetailResponse,
    RecipeIngredientResponse,
    RecipeListItemResponse,
    RecipeListResponse,
    RecipeStepResponse,
    WeeklyAiProposalResponse,
    WeeklyAiSlotChangeResponse,
    WeeklyGroceriesResponse,
    WeeklyMealPlanResponse,
    WeeklyMealSlotResponse,
    WeeklyRecipeSummaryResponse,
)
from src.app.commands.meal_planner import (
    AiAdjustMealPlanCommand,
    GenerateWeeklyMealPlanCommand,
    LogMealPlanSlotCommand,
    UpdateMealPlanPantryStockCommand,
    UpdateWeeklyMealPlanCommand,
)
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.app.queries.meal_planner import (
    GetCurrentWeeklyPlanQuery,
    GetRecipeDetailQuery,
    GetWeeklyGroceriesQuery,
    ListRecipesQuery,
)
from src.app.queries.user import GetUserTimezoneQuery
from src.app.services.catalog_meal_response_localizer import localize_catalog_meals
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
    WeeklyMealPlanNotFoundError,
)
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import WeeklyMealPlanPreferences
from src.domain.utils.timezone_utils import get_zone_info

router = APIRouter(tags=["Weekly Meal Plans", "Recipes"])


@router.get("/v1/meal-plans/current", response_model=WeeklyMealPlanResponse)
@limiter.limit("30/minute")
async def get_current_weekly_plan(
    request: Request,
    week_start_date: date | None = Query(default=None),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_text_translation_service),
):
    try:
        timezone = await _timezone(event_bus, request, user_id)
        week = _resolve_week(week_start_date, timezone)
        plan = await event_bus.send(
            GetCurrentWeeklyPlanQuery(user_id=user_id, week_start_date=week)
        )
        if plan is None:
            raise HTTPException(status_code=404, detail="Weekly meal plan not found")
        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/v1/meal-plans/generate",
    response_model=WeeklyMealPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def generate_weekly_plan(
    request: Request,
    body: GenerateWeeklyMealPlanRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_text_translation_service),
):
    try:
        timezone = await _timezone(event_bus, request, user_id)
        week = _resolve_week(body.week_start_date, timezone)
        budget = await event_bus.send(
            GetWeeklyBudgetQuery(
                user_id=user_id,
                target_date=datetime.now(get_zone_info(timezone)).date(),
                header_timezone=timezone,
            )
        )
        daily_calories = int(round(budget.get("adjusted_daily_calories") or 0))
        if daily_calories <= 0:
            raise HTTPException(
                status_code=503, detail="Weekly calorie target is unavailable"
            )
        preferences = WeeklyMealPlanPreferences.from_payload(
            body.preferences.model_dump(), people=body.people
        )
        plan = await event_bus.send(
            GenerateWeeklyMealPlanCommand(
                user_id=user_id,
                week_start_date=week,
                timezone=timezone,
                preferences=preferences,
                idempotency_key=_idempotency_key(idempotency_key),
                daily_calories=daily_calories,
            )
        )
        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/v1/meal-plans/{plan_id}", response_model=WeeklyMealPlanResponse)
@limiter.limit("30/minute")
async def update_weekly_plan(
    request: Request,
    plan_id: str,
    body: UpdateWeeklyMealPlanRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_text_translation_service),
):
    try:
        preferences = None
        if body.preferences is not None:
            preferences = WeeklyMealPlanPreferences.from_payload(
                body.preferences.model_dump(), people=body.people
            )
        slots = {
            (item.day_index, item.slot_index): item.recipe_id for item in body.slots
        }
        plan = await event_bus.send(
            UpdateWeeklyMealPlanCommand(
                user_id=user_id,
                plan_id=plan_id,
                idempotency_key=_idempotency_key(idempotency_key),
                people=body.people,
                preferences=preferences,
                status=body.status,
                slots=slots,
            )
        )
        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/v1/recipes", response_model=RecipeListResponse)
@limiter.limit("60/minute")
async def list_recipes(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    diet: str | None = Query(default=None),
    max_cook_time: int | None = Query(default=None, ge=0, le=600),
    cuisine: str | None = Query(default=None, max_length=80),
    allergies: str = Query(default="", max_length=1000),
    dislikes: str = Query(default="", max_length=1000),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_text_translation_service),
):
    del user_id
    try:
        page = await event_bus.send(
            ListRecipesQuery(
                query=q,
                diet=diet,
                max_cook_time=max_cook_time,
                cuisine=cuisine,
                allergies=_csv(allergies),
                dislikes=_csv(dislikes),
                limit=limit,
                offset=offset,
            )
        )
        meals = await localize_catalog_meals(
            page.items,
            language=get_request_language(request),
            translation_service=translation_service,
            include_ingredients=False,
        )
        return RecipeListResponse(
            items=[_recipe_list_item(meal) for meal in meals], total=page.total
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/v1/recipes/{recipe_id}", response_model=RecipeDetailResponse)
@limiter.limit("60/minute")
async def get_recipe_detail(
    request: Request,
    recipe_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_text_translation_service),
):
    del user_id
    try:
        meal = await event_bus.send(GetRecipeDetailQuery(recipe_id=recipe_id))
        if meal is None:
            raise HTTPException(status_code=404, detail="Recipe not found")
        localized = await localize_catalog_meals(
            (meal,),
            language=get_request_language(request),
            translation_service=translation_service,
        )
        return _recipe_detail(localized[0])
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/v1/meal-plans/{plan_id}/ai-prompt", response_model=WeeklyAiProposalResponse
)
@limiter.limit("10/minute")
async def ai_adjust_weekly_plan(
    request: Request,
    plan_id: str,
    body: AiAdjustMealPlanRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_text_translation_service),
):
    try:
        proposal = await event_bus.send(
            AiAdjustMealPlanCommand(
                user_id=user_id, plan_id=plan_id, prompt=body.prompt
            )
        )
        return WeeklyAiProposalResponse(
            explanation=proposal.explanation,
            diff_summary=proposal.diff_summary,
            proposed_plan=await _plan_response(
                proposal.proposed_plan,
                event_bus,
                language=get_request_language(request),
                translation_service=translation_service,
            ),
            slot_changes=[
                WeeklyAiSlotChangeResponse(**change) for change in proposal.slot_changes
            ],
        )
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get(
    "/v1/meal-plans/{plan_id}/groceries", response_model=WeeklyGroceriesResponse
)
@limiter.limit("30/minute")
async def get_weekly_groceries(
    request: Request,
    plan_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
):
    del request
    try:
        result = await event_bus.send(
            GetWeeklyGroceriesQuery(user_id=user_id, plan_id=plan_id)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Weekly meal plan not found")
        _, categories = result
        items = [item for category in categories for item in category.items]
        return WeeklyGroceriesResponse(
            total_items_count=len(items),
            to_buy_count=sum(item.status != "owned" for item in items),
            owned_count=sum(item.status == "owned" for item in items),
            categories=[
                GroceryCategoryResponse(
                    category=category.category,
                    items=[
                        GroceryItemResponse(**item.__dict__) for item in category.items
                    ],
                )
                for category in categories
            ],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/v1/meal-plans/{plan_id}/groceries", response_model=PantryUpdateResponse)
@limiter.limit("30/minute")
async def update_weekly_pantry(
    request: Request,
    plan_id: str,
    body: UpdateMealPlanPantryRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
):
    del request
    try:
        result = await event_bus.send(
            UpdateMealPlanPantryStockCommand(
                user_id=user_id,
                plan_id=plan_id,
                idempotency_key=_idempotency_key(idempotency_key),
                updates=[item.model_dump() for item in body.stock_updates],
            )
        )
        return PantryUpdateResponse(**result)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/v1/meal-plans/{plan_id}/slots/{slot_id}/log",
    response_model=LogMealPlanSlotResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def log_weekly_slot(
    request: Request,
    plan_id: str,
    slot_id: str,
    body: LogMealPlanSlotRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
):
    try:
        timezone = await _timezone(event_bus, request, user_id)
        result = await event_bus.send(
            LogMealPlanSlotCommand(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                idempotency_key=_idempotency_key(idempotency_key),
                meal_date=body.date,
                meal_type=body.meal_type,
                portion_multiplier=body.portion_multiplier,
                timezone=timezone,
            )
        )
        return LogMealPlanSlotResponse(**result.__dict__)
    except Exception as exc:
        raise _http_error(exc) from exc


async def _timezone(event_bus, request: Request, user_id: str) -> str:
    result = await event_bus.send(
        GetUserTimezoneQuery(
            user_id=user_id, header_timezone=request.headers.get("X-Timezone")
        )
    )
    return get_zone_info(result).key


def _resolve_week(value: date | None, timezone: str) -> date:
    if value is not None:
        if value.weekday() != 0:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "WEEK_START_NOT_MONDAY",
                    "message": "week_start_date must be a Monday",
                },
            )
        return value
    today = datetime.now(get_zone_info(timezone)).date()
    return today - timedelta(days=today.weekday())


async def _plan_response(
    plan,
    event_bus,
    *,
    language: str = "en",
    translation_service=None,
) -> WeeklyMealPlanResponse:
    recipe_ids = {slot.recipe_id for slot in plan.slots if slot.recipe_id}
    details = await asyncio.gather(
        *(
            event_bus.send(GetRecipeDetailQuery(recipe_id=recipe_id))
            for recipe_id in recipe_ids
        )
    )
    localized = await localize_catalog_meals(
        (meal for meal in details if meal is not None),
        language=language,
        translation_service=translation_service,
    )
    by_id = {meal.id: meal for meal in localized}
    grouped = []
    for day in range(7):
        grouped.append(
            [
                _slot_response(slot, by_id.get(slot.recipe_id))
                for slot in plan.slots
                if slot.day_index == day
            ]
        )
    to_buy_count = None
    if getattr(plan, "id", None):
        try:
            groceries_result = await event_bus.send(
                GetWeeklyGroceriesQuery(user_id=plan.user_id, plan_id=plan.id)
            )
            if groceries_result:
                _, categories = groceries_result
                to_buy_count = sum(
                    item.status != "owned" for cat in categories for item in cat.items
                )
        except Exception:
            to_buy_count = None

    return WeeklyMealPlanResponse(
        id=plan.id,
        week_start_date=plan.week_start_date,
        status=plan.status.value,
        people=plan.people,
        preferences=plan.preferences.to_dict(),
        plan=grouped,
        total_meals_planned=sum(slot.recipe_id is not None for slot in plan.slots),
        to_buy_count=to_buy_count,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _slot_response(slot, meal: CatalogMeal | None) -> WeeklyMealSlotResponse:
    return WeeklyMealSlotResponse(
        slot_id=slot.id,
        day_index=slot.day_index,
        slot_index=slot.slot_index,
        slot_name="lunch" if slot.slot_index == 0 else "dinner",
        recipe=_recipe_summary(meal) if meal else None,
        is_logged=slot.is_logged,
        logged_meal_id=slot.logged_meal_id,
    )


def _recipe_summary(meal: CatalogMeal) -> WeeklyRecipeSummaryResponse:
    return WeeklyRecipeSummaryResponse(
        id=meal.id,
        name=meal.name,
        image_url=meal.image_url,
        cook_time_minutes=meal.cook_time_minutes,
        calories=meal.calories,
    )


def _recipe_list_item(meal: CatalogMeal) -> RecipeListItemResponse:
    return RecipeListItemResponse(
        id=meal.id,
        slug=meal.catalog_key,
        name=meal.name,
        image_url=meal.image_url,
        cook_time_minutes=meal.cook_time_minutes,
        tag=meal.tag,
        calories=meal.calories,
        protein=float(meal.protein_g),
        carbs=float(meal.carbs_g),
        fat=float(meal.fat_g),
    )


def _recipe_detail(meal: CatalogMeal) -> RecipeDetailResponse:
    return RecipeDetailResponse(
        id=meal.id,
        name=meal.name,
        source_name=meal.source_name,
        source_url=meal.source_url,
        image_url=meal.image_url,
        prep_time_minutes=meal.prep_time_minutes,
        cook_time_minutes=meal.cook_time_minutes,
        tag=meal.tag,
        allergens=meal.allergens,
        summary=meal.summary or meal.description,
        equipment=meal.equipment,
        nutrition_per_serving=RecipeDetailNutritionResponse(
            calories=meal.calories,
            protein=float(meal.protein_g),
            carbs=float(meal.carbs_g),
            fat=float(meal.fat_g),
            fiber=float(meal.fiber_g),
        ),
        ingredients=[
            RecipeIngredientResponse(
                id=str(item.food_reference_id),
                name=item.name,
                amount_per_serving=float(item.quantity),
                unit=item.unit,
                category="fresh_produce"
                if item.category == "produce"
                else item.category,
            )
            for item in meal.ingredients
        ],
        steps=[RecipeStepResponse(**step.__dict__) for step in meal.steps],
    )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip().casefold() for item in value.split(",") if item.strip())


def _idempotency_key(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 160:
        raise HTTPException(
            status_code=400, detail="Idempotency-Key must contain 1 to 160 characters"
        )
    return value


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, WeeklyMealPlanNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": exc.error_code or "NOT_FOUND",
                "message": exc.message,
            },
        )
    if isinstance(exc, WeeklyMealPlanConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": exc.error_code or "CONFLICT",
                "message": exc.message,
            },
        )
    from src.api.exceptions import MealTrackException

    if isinstance(exc, MealTrackException):
        return create_http_exception(exc)
    return handle_exception(exc)
