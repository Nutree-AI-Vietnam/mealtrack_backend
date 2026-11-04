"""Weekly meal planner and recipe API routes."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from time import monotonic
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)

from src.api.base_dependencies import (
    get_async_food_reference_repository,
    get_planner_translation_service,
    weekly_plan_ai_dishes_enabled,
)
from src.api.dependencies.ai_jobs import get_ai_job_service
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.event_bus import get_configured_event_bus
from src.api.exceptions import create_http_exception, handle_exception
from src.api.mappers.food_reference_display_name import (
    resolve_grocery_proposal_display_name,
)
from src.api.middleware.accept_language import get_request_language
from src.api.middleware.rate_limit import limiter
from src.api.schemas.request.weekly_meal_planner_requests import (
    AiAdjustMealPlanRequest,
    GenerateWeeklyMealPlanRequest,
    LinkMealPlanSlotRequest,
    LogMealPlanSlotRequest,
    RecipeBatchRequest,
    UpdateGroceryDayLinesRequest,
    UpdateMealPlanPantryRequest,
    UpdateWeeklyMealPlanRequest,
    WeeklyPlannerMealType,
)
from src.api.schemas.response.weekly_meal_planner_responses import (
    GroceryCategoryResponse,
    GroceryItemResponse,
    LogMealPlanSlotResponse,
    PantryUpdateResponse,
    RecipeBatchResponse,
    RecipeDetailNutritionResponse,
    RecipeDetailResponse,
    RecipeIngredientResponse,
    RecipeItemsResponse,
    RecipeListItemResponse,
    RecipeListResponse,
    RecipeMacroSummaryResponse,
    RecipeStepResponse,
    WeeklyAiProposalGroceryItemResponse,
    WeeklyAiProposalResponse,
    WeeklyAiSlotChangeResponse,
    WeeklyDaySummaryResponse,
    WeeklyGroceriesResponse,
    WeeklyMealPlanResponse,
    WeeklyMealSlotResponse,
    WeeklyRecipeSummaryResponse,
)
from src.app.commands.meal_planner import (
    AiAdjustMealPlanCommand,
    GenerateWeeklyMealPlanCommand,
    LinkMealPlanSlotCommand,
    LogMealPlanSlotCommand,
    PrepareWeeklyMealPlanCommand,
    SetMealPlanSlotSkipCommand,
    UnlogMealPlanSlotCommand,
    UpdateGroceryDayLinesCommand,
    UpdateMealPlanPantryStockCommand,
    UpdateWeeklyMealPlanCommand,
)
from src.app.queries.get_weekly_budget_query import GetWeeklyBudgetQuery
from src.app.queries.meal_planner import (
    GetCurrentWeeklyPlanQuery,
    GetMealPlanSlotSuggestionsQuery,
    GetRecipeDetailQuery,
    GetRecipeDetailsQuery,
    GetRecipeSummariesQuery,
    GetWeeklyGroceriesQuery,
    ListRecipesQuery,
)
from src.app.queries.user import GetUserTimezoneQuery
from src.app.services.catalog_meal_response_localizer import (
    localize_catalog_meal_names,
    localize_catalog_meals,
    localize_grocery_categories,
)
from src.domain.constants.languages import normalize_language
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
    WeeklyMealPlanNotFoundError,
)
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros
from src.domain.model.weekly_meal_planner import (
    RICE_STEP_GRAMS,
    RICE_STEP_MACROS,
    SavedRecipeSnapshot,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanStatus,
    meal_type_for_slot,
)
from src.domain.services.nrf_score import nrf_coverage, nrf_quality
from src.domain.services.weekly_meal_planner.grocery_projection import (
    deterministic_ingredient_id,
)
from src.domain.utils.timezone_utils import (
    get_zone_info,
    is_valid_timezone,
    normalize_timezone,
)
from src.planner_observability import planner_timed
from src.planner_request_policy import planner_deadline, timeout_until

router = APIRouter(tags=["Weekly Meal Plans", "Recipes"])
logger = logging.getLogger(__name__)

# Sent in every plan payload. Add a name only when its endpoint ships.
WEEKLY_PLAN_FEATURES: tuple[str, ...] = (
    "slot_unlog",
    "slot_link",
    "slot_skip",
    "slot_suggestions",
    "recipe_labels",
    "recipe_batch",
    "saved_recipe_slots",
)
# Advertised only while the dishes come from a model: a client that does
# not see it will never be handed a plan in 'preparing'.
PLAN_PREPARING_FEATURE = "plan_preparing"


def _plan_features() -> list[str]:
    features = list(WEEKLY_PLAN_FEATURES)
    if weekly_plan_ai_dishes_enabled():
        features.append(PLAN_PREPARING_FEATURE)
    return features


# ai_jobs.id is VARCHAR(36) — one UUID, which is what every other job id
# is. "weekly-plan-" plus a plan's UUID is 48 characters, and staging threw
# StringDataRightTruncationError on the first real week: the plan was
# created, the job was not, and the planner sat in 'preparing' with nobody
# coming. Every test had used a short fake plan id, where it fitted.
#
# The id still has to be derived from the plan rather than minted fresh, so
# a second tab asking for the same week does not start a second write. A
# version 5 UUID over the plan id is both: stable, and 36 characters.
_WEEKLY_PLAN_JOB_NAMESPACE = uuid.UUID("6f1f1a4e-0f5f-4b2a-9a3b-0c4a2d9e7b10")


def weekly_plan_job_id(plan_id: str) -> str:
    """The job that writes this plan's dishes, named so it fits the column."""
    return str(uuid.uuid5(_WEEKLY_PLAN_JOB_NAMESPACE, f"weekly-plan-{plan_id}"))


async def _prepared_plan_response(
    request: Request,
    *,
    event_bus,
    user_id: str,
    week: date,
    timezone: str,
    preferences: WeeklyMealPlanPreferences,
    daily_calories: int,
    budget: dict | None,
    translation_service,
    include_grocery_count: bool,
    respond: bool = True,
):
    """Open the week and let a background job write its dishes.

    The plan row is created first so the planner has a week to show while
    the job runs. A job that cannot be queued leaves the plan in
    'preparing'; the next request queues it again, because the job id is
    derived from the plan and the request, not minted fresh.
    """
    plan = await event_bus.send(
        PrepareWeeklyMealPlanCommand(
            user_id=user_id,
            week_start_date=week,
            timezone=timezone,
            preferences=preferences,
            daily_calories=daily_calories,
        )
    )
    if plan.status.value == WeeklyMealPlanStatus.PREPARING.value:
        try:
            await get_ai_job_service().start_weekly_plan_job(
                job_id=weekly_plan_job_id(plan.id),
                user_id=user_id,
                language=get_request_language(request),
                request={
                    "plan_id": plan.id,
                    "week_start_date": week.isoformat(),
                    "daily_calories": daily_calories,
                    "timezone": timezone,
                    **_macro_targets(budget),
                },
            )
        except Exception:  # noqa: BLE001 - the week is still shown, empty
            logger.exception("Could not queue the write of plan %s", plan.id)
    if not respond:
        return None
    return await _plan_response(
        plan,
        event_bus,
        language=get_request_language(request),
        translation_service=translation_service,
        include_grocery_count=include_grocery_count,
        localization_timeout_seconds=1.5,
    )


# The last day of the plan week. Somebody who opens the app on Sunday has
# next week written before they go looking for it; anybody else still gets
# it the moment they ask, as before.
PREPARE_NEXT_WEEK_WEEKDAY = 6


async def _prepare_next_week(
    request,
    *,
    event_bus,
    user_id: str,
    week: date,
    timezone: str,
    plan,
) -> None:
    """Start next week's dishes while this week is being read.

    Only on the last day of the week, and only when next week has no plan.
    Writing a week takes minutes, so starting it a day early is the
    difference between a user seeing their week and watching it appear.

    Never raises: this is a head start, not part of the answer.
    """
    try:
        today = datetime.now(get_zone_info(timezone)).date()
        if today.weekday() != PREPARE_NEXT_WEEK_WEEKDAY or today < week:
            return
        next_week = week + timedelta(days=7)
        existing = await event_bus.send(
            GetCurrentWeeklyPlanQuery(user_id=user_id, week_start_date=next_week)
        )
        if existing is not None:
            return
        budget = await _weekly_budget(
            event_bus,
            GetWeeklyBudgetQuery(
                user_id=user_id, target_date=today, header_timezone=timezone
            ),
        )
        daily_calories = (
            int(round(budget.get("adjusted_daily_calories") or 0)) if budget else 0
        )
        if daily_calories <= 0:
            daily_calories = 2000
        await _prepared_plan_response(
            request,
            event_bus=event_bus,
            user_id=user_id,
            week=next_week,
            timezone=timezone,
            preferences=plan.preferences,
            daily_calories=daily_calories,
            budget=budget,
            translation_service=None,
            include_grocery_count=False,
            respond=False,
        )
    except Exception:  # noqa: BLE001 - this week's answer is what was asked for
        logger.warning("Could not start next week for user %s", user_id)


async def _ensure_the_week_is_being_written(
    request: Request,
    *,
    event_bus,
    user_id: str,
    plan,
    week: date,
    timezone: str,
) -> None:
    """Queue this plan's write when it is waiting and nothing is coming.

    _prepared_plan_response claims the next request queues the job again
    when queueing failed. It does not: the job is only queued where a plan
    is created, and a plan that already exists never reaches that branch.
    Staging's first real week proved it — the insert failed on a too-long
    id, and the plan sat in 'preparing' through every later refresh with
    nobody coming for it.

    The job id is derived from the plan, and creating it is ON CONFLICT DO
    NOTHING, so asking again is free once one exists. It is looked up first
    anyway, because the payload needs the day's budget and the app polls
    this endpoint every few seconds while a week is being written.
    """
    jobs = get_ai_job_service()
    job_id = weekly_plan_job_id(plan.id)
    try:
        await jobs.get(user_id=user_id, job_id=job_id)
        return
    except Exception:  # noqa: BLE001 - no job yet, or we cannot tell
        pass
    try:
        budget = await _weekly_budget(
            event_bus,
            GetWeeklyBudgetQuery(
                user_id=user_id,
                target_date=datetime.now(get_zone_info(timezone)).date(),
                header_timezone=timezone,
            ),
        )
        daily_calories = (
            int(round((budget or {}).get("adjusted_daily_calories") or 0)) or 2000
        )
        await jobs.start_weekly_plan_job(
            job_id=job_id,
            user_id=user_id,
            language=get_request_language(request),
            request={
                "plan_id": plan.id,
                "week_start_date": week.isoformat(),
                "daily_calories": daily_calories,
                "timezone": timezone,
                **_macro_targets(budget),
            },
        )
        logger.info("Re-queued the write of plan %s", plan.id)
    except Exception:  # noqa: BLE001 - the week is still shown as it is
        logger.exception("Could not re-queue the write of plan %s", plan.id)


@router.get("/v1/meal-plans/current", response_model=WeeklyMealPlanResponse)
@limiter.limit("30/minute")
async def get_current_weekly_plan(
    request: Request,
    week_start_date: date | None = Query(default=None),
    include_grocery_count: bool = Query(default=True),
    auto_generate: bool = Query(default=True),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    try:
        header_tz = request.headers.get("X-Timezone")
        timezone = (
            normalize_timezone(header_tz)
            if header_tz and is_valid_timezone(header_tz)
            else await _timezone(event_bus, request, user_id)
        )
        week = _resolve_week(week_start_date, timezone)
        plan = await event_bus.send(
            GetCurrentWeeklyPlanQuery(user_id=user_id, week_start_date=week)
        )
        if (
            plan is not None
            and auto_generate
            and getattr(plan.status, "value", plan.status)
            == WeeklyMealPlanStatus.PREPARING.value
            and weekly_plan_ai_dishes_enabled()
        ):
            await _ensure_the_week_is_being_written(
                request,
                event_bus=event_bus,
                user_id=user_id,
                plan=plan,
                week=week,
                timezone=timezone,
            )
        if plan is None:
            if not auto_generate:
                raise HTTPException(
                    status_code=404, detail="Weekly meal plan not found"
                )
            budget = await _weekly_budget(
                event_bus,
                GetWeeklyBudgetQuery(
                    user_id=user_id,
                    target_date=datetime.now(get_zone_info(timezone)).date(),
                    header_timezone=timezone,
                ),
            )
            daily_calories = (
                int(round(budget.get("adjusted_daily_calories") or 0)) if budget else 0
            )
            if daily_calories <= 0:
                daily_calories = 2000
            preferences = WeeklyMealPlanPreferences(people=1)
            if weekly_plan_ai_dishes_enabled():
                return await _prepared_plan_response(
                    request,
                    event_bus=event_bus,
                    user_id=user_id,
                    week=week,
                    timezone=timezone,
                    preferences=preferences,
                    daily_calories=daily_calories,
                    budget=budget,
                    translation_service=translation_service,
                    include_grocery_count=include_grocery_count,
                )
            plan = await event_bus.send(
                GenerateWeeklyMealPlanCommand(
                    user_id=user_id,
                    week_start_date=week,
                    timezone=timezone,
                    preferences=preferences,
                    idempotency_key=_idempotency_key(
                        f"auto-gen-{user_id}-{week.isoformat()}"
                    ),
                    daily_calories=daily_calories,
                    **_macro_targets(budget),
                )
            )

        if weekly_plan_ai_dishes_enabled():
            await _prepare_next_week(
                request,
                event_bus=event_bus,
                user_id=user_id,
                week=week,
                timezone=timezone,
                plan=plan,
            )

        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
            include_grocery_count=include_grocery_count,
            localization_timeout_seconds=1.5,
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
@limiter.limit("10/minute")
async def generate_weekly_plan(
    request: Request,
    body: GenerateWeeklyMealPlanRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    include_grocery_count: bool = Query(default=True),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    try:
        header_tz = request.headers.get("X-Timezone")
        timezone = (
            normalize_timezone(header_tz)
            if header_tz and is_valid_timezone(header_tz)
            else await _timezone(event_bus, request, user_id)
        )
        week = _resolve_week(body.week_start_date, timezone)
        budget = await _weekly_budget(
            event_bus,
            GetWeeklyBudgetQuery(
                user_id=user_id,
                target_date=datetime.now(get_zone_info(timezone)).date(),
                header_timezone=timezone,
            ),
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
                **_macro_targets(budget),
            )
        )
        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
            include_grocery_count=include_grocery_count,
            localization_timeout_seconds=1.5,
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
    include_grocery_count: bool = Query(default=True),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    try:
        preferences = None
        if body.preferences is not None:
            preferences = WeeklyMealPlanPreferences.from_payload(
                body.preferences.model_dump(), people=body.people
            )
        slots = {
            (item.day_index, item.slot_index): item.recipe_id
            for item in body.slots
            if item.saved_suggestion_id is None
        }
        saved_slots = {
            (item.day_index, item.slot_index): item.saved_suggestion_id
            for item in body.slots
            if item.saved_suggestion_id is not None
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
                expected_revision=body.expected_revision,
                saved_slots=saved_slots or None,
            )
        )
        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
            include_grocery_count=include_grocery_count,
            localization_timeout_seconds=1.5,
        )
    except HTTPException:
        raise
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
    meal_type: WeeklyPlannerMealType | None = Query(default=None),
    allergies: str = Query(default="", max_length=1000),
    dislikes: str = Query(default="", max_length=1000),
    min_protein: float | None = Query(default=None, ge=0, le=500),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    del user_id
    try:
        page = await event_bus.send(
            ListRecipesQuery(
                query=q,
                diet=diet,
                max_cook_time=max_cook_time,
                cuisine=cuisine,
                meal_type=meal_type,
                allergies=_csv(allergies),
                dislikes=_csv(dislikes),
                limit=limit,
                offset=offset,
                min_protein=min_protein,
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
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


# Declared before /v1/recipes/{recipe_id} so "batch" is never read as an id.
@router.post("/v1/recipes/batch", response_model=RecipeBatchResponse)
@limiter.limit("30/minute")
async def get_recipe_batch(
    request: Request,
    body: RecipeBatchRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    """Up to 30 recipe details in request order; unknown ids come back in missing."""
    del user_id
    try:
        meals = await event_bus.send(GetRecipeDetailsQuery(recipe_ids=tuple(body.ids)))
        localized = (
            await localize_catalog_meals(
                tuple(meals),
                language=get_request_language(request),
                translation_service=translation_service,
            )
            if meals
            else ()
        )
        found = {meal.id for meal in localized}
        return RecipeBatchResponse(
            items=[_recipe_detail(meal) for meal in localized],
            missing=[recipe_id for recipe_id in body.ids if recipe_id not in found],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/v1/recipes/{recipe_id}", response_model=RecipeDetailResponse)
@limiter.limit("60/minute")
async def get_recipe_detail(
    request: Request,
    recipe_id: str,
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    try:
        return await _recipe_detail_response(
            request,
            recipe_id,
            enrich_micronutrients=False,
            event_bus=event_bus,
            translation_service=translation_service,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/v1/recipes/{recipe_id}/micronutrients/enrich",
    response_model=RecipeDetailResponse,
    deprecated=True,
)
@limiter.limit("10/minute")
async def deprecated_enrich_recipe_detail_micronutrients(
    request: Request,
    recipe_id: str,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    """Compatibility endpoint; returns persisted detail without generating."""
    del user_id
    try:
        return await _recipe_detail_response(
            request,
            recipe_id,
            enrich_micronutrients=True,
            event_bus=event_bus,
            translation_service=translation_service,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


def _bounded_ai_request(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        request = kwargs.get("request") or next(
            (arg for arg in args if isinstance(arg, Request)), None
        )
        started = (
            getattr(request.state, "planner_started_at", monotonic())
            if request is not None
            else monotonic()
        )
        deadline = started + 30.0
        try:
            with planner_deadline(deadline):
                async with timeout_until(deadline):
                    return await function(*args, **kwargs)
        except TimeoutError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "AI_MEAL_PLAN_UNAVAILABLE",
                    "message": "AI meal plan adjustment exceeded its deadline",
                },
            ) from exc

    return wrapped


@router.post(
    "/v1/meal-plans/{plan_id}/ai-prompt", response_model=WeeklyAiProposalResponse
)
@limiter.limit("10/minute")
@_bounded_ai_request
async def ai_adjust_weekly_plan(
    request: Request,
    plan_id: str,
    body: AiAdjustMealPlanRequest,
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
    food_reference_repository=Depends(get_async_food_reference_repository),
):
    try:
        language = get_request_language(request)
        proposal = await event_bus.send(
            AiAdjustMealPlanCommand(
                user_id=user_id,
                plan_id=plan_id,
                prompt=body.prompt,
                language=language,
                target_day_index=body.target_day_index,
                target_slot_index=body.target_slot_index,
            )
        )
        explanation, diff_summary = proposal.explanation, proposal.diff_summary
        grocery_names_by_id: dict[int, str] = {}
        if normalize_language(language) == "vi" and proposal.proposed_groceries:
            source_names_by_id = {
                item.ingredient_id: item.name for item in proposal.proposed_groceries
            }
            grocery_ids = list(source_names_by_id)
            # The English projection mode avoids serving-label translation lookups;
            # name_vi is included independently as authored catalog metadata.
            try:
                grocery_projections = (
                    await food_reference_repository.get_display_projections(
                        grocery_ids, language="en"
                    )
                )
            except Exception:
                logger.exception(
                    "Could not load catalog labels for meal-plan proposal groceries"
                )
                grocery_projections = {}
            grocery_names_by_id = {
                ingredient_id: resolve_grocery_proposal_display_name(
                    grocery_projections.get(ingredient_id),
                    source_names_by_id[ingredient_id],
                    language,
                )
                for ingredient_id in grocery_ids
            }
        return WeeklyAiProposalResponse(
            base_revision=proposal.base_revision,
            explanation=explanation,
            diff_summary=diff_summary,
            proposed_plan=await _plan_response(
                proposal.proposed_plan,
                event_bus,
                language=language,
                translation_service=translation_service,
                include_grocery_count=False,
            ),
            slot_changes=[
                WeeklyAiSlotChangeResponse(**change) for change in proposal.slot_changes
            ],
            proposed_groceries=[
                WeeklyAiProposalGroceryItemResponse(
                    ingredient_id=item.ingredient_id,
                    name=grocery_names_by_id.get(
                        item.ingredient_id,
                        resolve_grocery_proposal_display_name(
                            None, item.name, language
                        ),
                    ),
                    category=item.category,
                    total_needed=item.total_needed,
                    unit=item.unit,
                )
                for item in proposal.proposed_groceries
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
    translation_service=Depends(get_planner_translation_service),
):
    try:
        result = await event_bus.send(
            GetWeeklyGroceriesQuery(user_id=user_id, plan_id=plan_id)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Weekly meal plan not found")
        _, categories = result
        try:
            localized_categories = await asyncio.wait_for(
                localize_grocery_categories(
                    categories,
                    language=get_request_language(request),
                    translation_service=translation_service,
                ),
                timeout=1.5,
            )
        except TimeoutError:
            logger.info("Grocery localization timed out; using catalog names")
            localized_categories = categories
        except Exception:
            logger.warning("Grocery localization failed; using catalog names")
            localized_categories = categories
        items = [item for category in localized_categories for item in category.items]
        return WeeklyGroceriesResponse(
            total_items_count=len(items),
            to_buy_count=sum(item.status in {"needed", "need_more"} for item in items),
            owned_count=sum(item.status == "owned" for item in items),
            categories=[
                GroceryCategoryResponse(
                    category=category.category,
                    items=[
                        GroceryItemResponse(**item.__dict__) for item in category.items
                    ],
                )
                for category in localized_categories
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


@router.patch(
    "/v1/meal-plans/{plan_id}/grocery-days",
    response_model=PantryUpdateResponse,
)
@limiter.limit("30/minute")
async def update_grocery_day_lines(
    request: Request,
    plan_id: str,
    body: UpdateGroceryDayLinesRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
):
    del request
    try:
        result = await event_bus.send(
            UpdateGroceryDayLinesCommand(
                user_id=user_id,
                plan_id=plan_id,
                idempotency_key=_idempotency_key(idempotency_key),
                ingredient_id=body.ingredient_id,
                lines=[item.model_dump() for item in body.lines],
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
        header_tz = request.headers.get("X-Timezone")
        timezone = (
            normalize_timezone(header_tz)
            if header_tz and is_valid_timezone(header_tz)
            else None
        )
        result = await event_bus.send(
            LogMealPlanSlotCommand(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                idempotency_key=_idempotency_key(idempotency_key),
                meal_date=body.date,
                meal_type=body.meal_type,
                expected_recipe_id=body.expected_recipe_id,
                portion_multiplier=body.portion_multiplier,
                rice_steps=body.rice_steps,
                timezone=timezone,
            )
        )
        return LogMealPlanSlotResponse(**result.__dict__)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/v1/meal-plans/{plan_id}/slots/{slot_id}/log",
    response_model=WeeklyMealPlanResponse,
)
@limiter.limit("20/minute")
async def unlog_weekly_slot(
    request: Request,
    plan_id: str,
    slot_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    include_grocery_count: bool = Query(default=True),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    """Release a logged slot; delete its diary meal only if the plan logged it."""
    try:
        plan = await event_bus.send(
            UnlogMealPlanSlotCommand(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                idempotency_key=_idempotency_key(idempotency_key),
            )
        )
        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
            include_grocery_count=include_grocery_count,
            localization_timeout_seconds=1.5,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/v1/meal-plans/{plan_id}/slots/{slot_id}/link",
    response_model=WeeklyMealPlanResponse,
)
@limiter.limit("20/minute")
async def link_weekly_slot(
    request: Request,
    plan_id: str,
    slot_id: str,
    body: LinkMealPlanSlotRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    include_grocery_count: bool = Query(default=True),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    """Mark a slot eaten with a meal already in the user's diary."""
    try:
        plan = await event_bus.send(
            LinkMealPlanSlotCommand(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                idempotency_key=_idempotency_key(idempotency_key),
                meal_id=body.meal_id,
                timezone=_header_timezone(request),
            )
        )
        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
            include_grocery_count=include_grocery_count,
            localization_timeout_seconds=1.5,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/v1/meal-plans/{plan_id}/slots/{slot_id}/skip",
    response_model=WeeklyMealPlanResponse,
)
@limiter.limit("30/minute")
async def skip_weekly_slot(
    request: Request,
    plan_id: str,
    slot_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    include_grocery_count: bool = Query(default=True),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    """Skip a slot: it stays planned but buys no groceries."""
    return await _set_slot_skip(
        request,
        plan_id,
        slot_id,
        skipped=True,
        idempotency_key=idempotency_key,
        include_grocery_count=include_grocery_count,
        user_id=user_id,
        event_bus=event_bus,
        translation_service=translation_service,
    )


@router.delete(
    "/v1/meal-plans/{plan_id}/slots/{slot_id}/skip",
    response_model=WeeklyMealPlanResponse,
)
@limiter.limit("30/minute")
async def unskip_weekly_slot(
    request: Request,
    plan_id: str,
    slot_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    include_grocery_count: bool = Query(default=True),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    return await _set_slot_skip(
        request,
        plan_id,
        slot_id,
        skipped=False,
        idempotency_key=idempotency_key,
        include_grocery_count=include_grocery_count,
        user_id=user_id,
        event_bus=event_bus,
        translation_service=translation_service,
    )


async def _set_slot_skip(
    request: Request,
    plan_id: str,
    slot_id: str,
    *,
    skipped: bool,
    idempotency_key: str,
    include_grocery_count: bool,
    user_id: str,
    event_bus,
    translation_service,
) -> WeeklyMealPlanResponse:
    try:
        plan = await event_bus.send(
            SetMealPlanSlotSkipCommand(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                idempotency_key=_idempotency_key(idempotency_key),
                skipped=skipped,
            )
        )
        return await _plan_response(
            plan,
            event_bus,
            language=get_request_language(request),
            translation_service=translation_service,
            include_grocery_count=include_grocery_count,
            localization_timeout_seconds=1.5,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get(
    "/v1/meal-plans/{plan_id}/slots/{slot_id}/suggestions",
    response_model=RecipeItemsResponse,
)
@limiter.limit("30/minute")
async def get_weekly_slot_suggestions(
    request: Request,
    plan_id: str,
    slot_id: str,
    diet: Literal["vegetarian", "no-pork"] | None = Query(default=None),
    max_cook_time: int | None = Query(default=None, ge=0, le=600),
    min_protein: float | None = Query(default=None, ge=0, le=500),
    limit: int = Query(default=10, ge=1, le=30),
    user_id: str = Depends(get_current_user_id),
    event_bus=Depends(get_configured_event_bus),
    translation_service=Depends(get_planner_translation_service),
):
    """Recipes a PATCH would accept for this slot, minus the week's recipes."""
    try:
        meals = await event_bus.send(
            GetMealPlanSlotSuggestionsQuery(
                user_id=user_id,
                plan_id=plan_id,
                slot_id=slot_id,
                diet=diet,
                max_cook_time=max_cook_time,
                min_protein=min_protein,
                limit=limit,
            )
        )
        localized = await localize_catalog_meals(
            meals,
            language=get_request_language(request),
            translation_service=translation_service,
            include_ingredients=False,
        )
        return RecipeItemsResponse(
            items=[_recipe_list_item(meal) for meal in localized]
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


def _header_timezone(request: Request) -> str | None:
    header_tz = request.headers.get("X-Timezone")
    if header_tz and is_valid_timezone(header_tz):
        return normalize_timezone(header_tz)
    return None


async def _timezone(event_bus, request: Request, user_id: str) -> str:
    result = await event_bus.send(
        GetUserTimezoneQuery(
            user_id=user_id, header_timezone=request.headers.get("X-Timezone")
        )
    )
    return get_zone_info(result).key


def _resolve_week(value: date | None, timezone: str) -> date:
    today = datetime.now(get_zone_info(timezone)).date()
    current_monday = today - timedelta(days=today.weekday())
    if value is not None:
        if value.weekday() != 0:
            raise HTTPException(
                status_code=422,
                detail={
                    "error_code": "WEEK_START_NOT_MONDAY",
                    "message": "week_start_date must be a Monday",
                },
            )
        # Next week is open so the app can get it ready before the weekend
        # shop; anything later stays closed.
        if value > current_monday + timedelta(days=7):
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "WEEK_PLAN_FUTURE_NOT_ALLOWED",
                    "message": "Only past, current or next week meal plans "
                    "are available.",
                },
            )
        return value
    return current_monday


@planner_timed("response_projection")
async def _plan_response(
    plan,
    event_bus,
    *,
    language: str = "en",
    translation_service=None,
    include_grocery_count: bool = True,
    localization_timeout_seconds: float | None = 1.5,
) -> WeeklyMealPlanResponse:
    recipe_ids = {slot.recipe_id for slot in plan.slots if slot.recipe_id}
    summaries = (
        await event_bus.send(
            GetRecipeSummariesQuery(recipe_ids=tuple(sorted(recipe_ids)))
        )
        if recipe_ids
        else ()
    )
    try:
        localization = localize_catalog_meal_names(
            summaries,
            language=language,
            translation_service=translation_service,
        )
        localized = (
            await asyncio.wait_for(localization, timeout=localization_timeout_seconds)
            if localization_timeout_seconds is not None
            else await localization
        )
    except TimeoutError:
        logger.info("Meal-plan name localization timed out; using catalog names")
        localized = summaries
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
    if include_grocery_count and getattr(plan, "id", None):
        try:
            groceries_result = await event_bus.send(
                GetWeeklyGroceriesQuery(
                    user_id=plan.user_id, plan_id=plan.id, plan=plan
                )
            )
            if groceries_result:
                _, categories = groceries_result
                to_buy_count = sum(
                    item.status in {"needed", "need_more"}
                    for cat in categories
                    for item in cat.items
                )
        except Exception:
            to_buy_count = None

    return WeeklyMealPlanResponse(
        id=plan.id,
        week_start_date=plan.week_start_date,
        revision=plan.revision,
        status=plan.status.value,
        people=plan.people,
        preferences=plan.preferences.to_dict(),
        plan=grouped,
        days=_day_summaries(plan, by_id),
        total_meals_planned=sum(slot.has_recipe for slot in plan.slots),
        to_buy_count=to_buy_count,
        algorithm_version=getattr(plan, "algorithm_version", None),
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        features=_plan_features(),
    )


# A gap smaller than this is not worth telling anybody to eat. Below it the
# day counts as on target.
SNACK_WORTH_EATING_KCAL = 80


def _slot_calories(slot, meal: CatalogMeal | None) -> float:
    """What the plan asks the user to eat in this slot, dish plus staple."""
    saved = getattr(slot, "saved_recipe", None)
    portion = float(getattr(slot, "planned_portion", 1.0) or 1.0)
    steps = int(getattr(slot, "planned_rice_steps", 0) or 0)
    if saved is not None:
        base = float(getattr(saved, "calories", 0) or 0)
    elif meal is not None:
        base = float(meal.calories or 0)
    else:
        return 0.0
    rice_protein, rice_carbs, rice_fat = RICE_STEP_MACROS
    rice = Macros.raw_total_calories(
        rice_protein * steps, rice_carbs * steps, rice_fat * steps
    )
    return base * portion + rice


def _day_summaries(plan, by_id) -> list[WeeklyDaySummaryResponse]:
    """Per-day calories, and the gap a snack would close.

    Reported only where it is worth eating. A day on or over target carries
    no snack, so a client can show the prompt on exactly the days that need
    one without deciding the threshold itself.
    """
    target = getattr(plan, "daily_calories", None)
    summaries: list[WeeklyDaySummaryResponse] = []
    for day in range(7):
        planned = sum(
            _slot_calories(slot, by_id.get(slot.recipe_id))
            for slot in plan.slots
            if slot.day_index == day
        )
        gap = None
        if target and planned > 0:
            short = float(target) - planned
            if short >= SNACK_WORTH_EATING_KCAL:
                gap = int(round(short / 10) * 10)
        summaries.append(
            WeeklyDaySummaryResponse(
                day_index=day,
                planned_calories=int(round(planned)),
                target_calories=int(target) if target else None,
                snack_calories=gap,
            )
        )
    return summaries


def _slot_response(slot, meal: CatalogMeal | None) -> WeeklyMealSlotResponse:
    saved_recipe = getattr(slot, "saved_recipe", None)
    recipe: WeeklyRecipeSummaryResponse | None
    if saved_recipe is not None:
        recipe = _saved_recipe_summary(saved_recipe)
    else:
        recipe = _recipe_summary(meal) if meal else None
    return WeeklyMealSlotResponse(
        slot_id=slot.id,
        day_index=slot.day_index,
        slot_index=slot.slot_index,
        slot_name=meal_type_for_slot(slot.slot_index),
        recipe=recipe,
        is_logged=slot.is_logged,
        logged_meal_id=slot.logged_meal_id,
        log_source=getattr(slot, "log_source", None),
        is_skipped=bool(getattr(slot, "is_skipped", False)),
        planned_portion=float(getattr(slot, "planned_portion", 1.0) or 1.0),
        planned_rice_steps=int(getattr(slot, "planned_rice_steps", 0) or 0),
        planned_rice_grams=int(getattr(slot, "planned_rice_steps", 0) or 0)
        * RICE_STEP_GRAMS,
    )


def _macro_targets(budget: dict | None) -> dict[str, float]:
    """The day's protein, carb and fat targets, when the budget has them."""
    names = {
        "daily_protein": "daily_protein",
        "daily_carbs": "adjusted_daily_carbs",
        "daily_fat": "adjusted_daily_fat",
    }
    targets = {}
    for field, key in names.items():
        value = (budget or {}).get(key)
        if value:
            targets[field] = float(value)
    return targets


def _recipe_summary(meal: CatalogMeal) -> WeeklyRecipeSummaryResponse:
    return WeeklyRecipeSummaryResponse(
        id=meal.id,
        name=meal.name,
        image_url=meal.image_url,
        cook_time_minutes=meal.cook_time_minutes,
        calories=meal.calories,
        nutrition_per_serving=RecipeMacroSummaryResponse(
            calories=float(meal.calories),
            protein=float(meal.protein_g),
            carbs=float(meal.carbs_g),
            fat=float(meal.fat_g),
            fiber=float(meal.fiber_g),
        ),
        is_vegetarian=meal.diet_labels.is_vegetarian,
        is_pork_free=meal.diet_labels.is_pork_free,
        kind="catalog",
    )


def _saved_recipe_summary(recipe: SavedRecipeSnapshot) -> WeeklyRecipeSummaryResponse:
    """The slot's copy of a saved recipe, in the catalog summary shape."""
    return WeeklyRecipeSummaryResponse(
        id=recipe.suggestion_id,
        name=recipe.name,
        image_url=recipe.image_url,
        cook_time_minutes=recipe.cook_time_minutes,
        calories=recipe.calories,
        nutrition_per_serving=RecipeMacroSummaryResponse(
            calories=float(recipe.calories),
            protein=recipe.protein,
            carbs=recipe.carbs,
            fat=recipe.fat,
            fiber=recipe.fiber,
        ),
        kind="saved",
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
        is_vegetarian=meal.diet_labels.is_vegetarian,
        is_pork_free=meal.diet_labels.is_pork_free,
    )


async def _recipe_detail_response(
    request: Request,
    recipe_id: str,
    *,
    enrich_micronutrients: bool,
    event_bus,
    translation_service,
) -> RecipeDetailResponse:
    meal = await event_bus.send(
        GetRecipeDetailQuery(
            recipe_id=recipe_id,
            enrich_micronutrients=enrich_micronutrients,
        )
    )
    if meal is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    localized = await localize_catalog_meals(
        (meal,),
        language=get_request_language(request),
        translation_service=translation_service,
    )
    return _recipe_detail(localized[0])


def _recipe_detail(meal: CatalogMeal) -> RecipeDetailResponse:
    micros = meal.nutrition_micros
    score = (
        round(nrf_quality(float(meal.protein_g), float(meal.fiber_g), micros))
        if (
            isinstance(micros, Micros)
            and not meal.nutrition_micros_estimated
            and nrf_coverage(micros) >= 1
        )
        else None
    )
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
        base_servings=meal.base_servings,
        serving_confidence=meal.serving_confidence,
        nutrition_per_serving=RecipeDetailNutritionResponse(
            calories=meal.calories,
            protein=float(meal.protein_g),
            carbs=float(meal.carbs_g),
            fat=float(meal.fat_g),
            fiber=float(meal.fiber_g),
            micros=micros.to_dict() if isinstance(micros, Micros) else {},
            micros_sources=meal.nutrition_micros_sources,
            micros_estimated=meal.nutrition_micros_estimated,
            micros_enrichment_loaded=meal.nutrition_micros_enrichment_loaded,
            score=score,
        ),
        ingredients=[
            RecipeIngredientResponse(
                id=(
                    str(item.food_reference_id)
                    if item.food_reference_id is not None
                    else str(deterministic_ingredient_id(item.name))
                ),
                name=item.name,
                amount_per_serving=float(item.quantity),
                unit=item.unit,
                category="fresh_produce"
                if item.category == "produce"
                else item.category,
                canonical_amount=float(item.canonical_amount)
                if item.canonical_amount is not None
                else None,
                canonical_unit=item.canonical_unit,
                quantity_dimension=item.quantity_dimension,
                quantity_confidence=item.quantity_confidence,
            )
            for item in meal.ingredients
        ],
        steps=[RecipeStepResponse(**step.__dict__) for step in meal.steps],
    )


def _csv(value: str) -> tuple[str, ...]:
    return tuple(
        item.strip().casefold() for item in re.split(r"[,;]", value) if item.strip()
    )


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


@planner_timed("budget")
async def _weekly_budget(event_bus, query):
    return await event_bus.send(query)
