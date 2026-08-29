"""Support models and helpers for meal recommendation routes."""

from __future__ import annotations

from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from src.api.mappers.catalog_meal_mapper import (
    catalog_meal_ingredients,
    catalog_meal_response,
)
from src.api.schemas.response.meal_recommendation_responses import (
    MealRecommendationAlternativeResponse,
    MealRecommendationCatalogMealResponse,
    MealRecommendationCatalogMealSummaryResponse,
    MealRecommendationIngredientResponse,
    MealRecommendationMacrosResponse,
    MealRecommendationPlanResponse,
    MealRecommendationPlanSummaryResponse,
    MealRecommendationSlotDetailResponse,
    MealRecommendationSlotResponse,
    MealRecommendationSlotSummaryResponse,
)
from src.app.services.meal_recommendation_analytics_service import (
    MealRecommendationAnalyticsService,
)
from src.domain.model.meal_recommendation import PersistedMealRecommendationPlan
from src.domain.model.meal_recommendation.catalog_recipe import CatalogMeal
from src.observability import distribution_metric, increment_metric


class SwapMealRecommendationSlotRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=160)
    expected_selection_version: int = Field(..., ge=1)
    alternative_catalog_meal_id: str | None = None
    reason: Literal["user_requested", "alternative_selected"] = "user_requested"

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id is required")
        return normalized


class LogRecommendedMealRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=160)

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id is required")
        return normalized


class SkipMealRecommendationSlotRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=160)

    @field_validator("request_id")
    @classmethod
    def normalize_request_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("request_id is required")
        return normalized


def record_operation_latency(
    operation: str,
    started: float,
    status_value: str,
    *,
    outcome: str | None = None,
) -> None:
    elapsed_ms = (perf_counter() - started) * 1000
    attributes = {
        "component": "meal_recommendation",
        "operation": operation,
        "status": status_value,
    }
    if outcome is not None:
        attributes["outcome"] = outcome
    distribution_metric(
        "meal_recommendation.operation.latency_ms",
        elapsed_ms,
        unit="millisecond",
        attributes=attributes,
    )
    increment_metric("meal_recommendation.requests", attributes=attributes)


async def capture_plan_events(
    analytics_service: MealRecommendationAnalyticsService,
    *,
    user_id: str,
    plan: PersistedMealRecommendationPlan,
    events: tuple[str, ...],
) -> None:
    for event in events:
        await analytics_service.capture_plan_response(
            user_id=user_id,
            event=event,
            plan=plan,
        )


async def capture_slot_event(
    analytics_service: MealRecommendationAnalyticsService,
    *,
    user_id: str,
    event: str,
    plan_id: str,
) -> None:
    await analytics_service.capture_slot_response(
        user_id=user_id,
        event=event,
        plan_id=plan_id,
    )


def to_response(
    plan: PersistedMealRecommendationPlan,
) -> MealRecommendationPlanResponse:
    return MealRecommendationPlanResponse(
        id=plan.id,
        status=plan.status,
        timezone=plan.timezone,
        start_date=plan.start_date,
        daily_calories=plan.daily_calories,
        allergy_evaluated=False,
        slots=[
            MealRecommendationSlotResponse(
                id=slot.id,
                slot_date=slot.slot_date,
                day_index=slot.day_index,
                meal_type=slot.meal_type,
                catalog_meal_id=slot.catalog_meal_id,
                catalog_meal=_catalog_meal_response(_selected_catalog_meal(slot)),
                target_calories=slot.target_calories,
                score=slot.score,
                position=slot.position,
                selection_version=slot.selection_version,
                logged_meal_id=slot.logged_meal_id,
                shown_at=slot.shown_at,
                skipped_at=slot.skipped_at,
                alternatives=[
                    MealRecommendationAlternativeResponse(
                        id=alternative.id,
                        catalog_meal_id=alternative.catalog_meal_id,
                        catalog_meal=_catalog_meal_response(
                            _required_catalog_meal(alternative.catalog_meal)
                        ),
                        score=alternative.score,
                        candidate_rank=alternative.candidate_rank,
                    )
                    for alternative in slot.alternatives
                ],
            )
            for slot in plan.slots
        ],
    )


def to_summary_response(
    plan: PersistedMealRecommendationPlan,
) -> MealRecommendationPlanSummaryResponse:
    return MealRecommendationPlanSummaryResponse(
        id=plan.id,
        status=plan.status,
        timezone=plan.timezone,
        start_date=plan.start_date,
        daily_calories=plan.daily_calories,
        allergy_evaluated=False,
        slots=[
            MealRecommendationSlotSummaryResponse(
                id=slot.id,
                slot_date=slot.slot_date,
                day_index=slot.day_index,
                meal_type=slot.meal_type,
                catalog_meal_id=slot.catalog_meal_id,
                catalog_meal=_catalog_meal_summary_response(
                    _selected_catalog_meal(slot)
                ),
                target_calories=slot.target_calories,
                position=slot.position,
                selection_version=slot.selection_version,
                logged_meal_id=slot.logged_meal_id,
                shown_at=slot.shown_at,
                skipped_at=slot.skipped_at,
            )
            for slot in plan.slots
        ],
    )


def to_slot_detail_response(
    plan_id: str,
    slot,
) -> MealRecommendationSlotDetailResponse:
    return MealRecommendationSlotDetailResponse(
        plan_id=plan_id,
        slot=MealRecommendationSlotResponse(
            id=slot.id,
            slot_date=slot.slot_date,
            day_index=slot.day_index,
            meal_type=slot.meal_type,
            catalog_meal_id=slot.catalog_meal_id,
            catalog_meal=_catalog_meal_response(_selected_catalog_meal(slot)),
            target_calories=slot.target_calories,
            score=slot.score,
            position=slot.position,
            selection_version=slot.selection_version,
            logged_meal_id=slot.logged_meal_id,
            shown_at=slot.shown_at,
            skipped_at=slot.skipped_at,
            alternatives=[
                MealRecommendationAlternativeResponse(
                    id=alternative.id,
                    catalog_meal_id=alternative.catalog_meal_id,
                    catalog_meal=_catalog_meal_response(
                        _required_catalog_meal(alternative.catalog_meal)
                    ),
                    score=alternative.score,
                    candidate_rank=alternative.candidate_rank,
                )
                for alternative in slot.alternatives
            ],
        ),
    )


def _selected_catalog_meal(slot) -> CatalogMeal:
    if slot.selected is not None and slot.selected.catalog_meal is not None:
        return slot.selected.catalog_meal
    raise ValueError(f"catalog meal details are missing for slot {slot.id}")


def _required_catalog_meal(catalog_meal: CatalogMeal | None) -> CatalogMeal:
    if catalog_meal is None:
        raise ValueError(
            "catalog meal details are missing for recommendation candidate"
        )
    return catalog_meal


def _catalog_meal_response(
    catalog_meal: CatalogMeal,
) -> MealRecommendationCatalogMealResponse:
    return catalog_meal_response(catalog_meal)


def _catalog_meal_summary_response(
    catalog_meal: CatalogMeal,
) -> MealRecommendationCatalogMealSummaryResponse:
    return MealRecommendationCatalogMealSummaryResponse(
        id=catalog_meal.id,
        name=catalog_meal.name,
        cuisine=catalog_meal.cuisine,
        image_url=catalog_meal.image_url,
        calories=catalog_meal.calories,
        macros=MealRecommendationMacrosResponse(
            protein_g=float(catalog_meal.protein_g),
            carbs_g=float(catalog_meal.carbs_g),
            fat_g=float(catalog_meal.fat_g),
            fiber_g=float(catalog_meal.fiber_g),
            sugar_g=float(catalog_meal.sugar_g),
        ),
        ingredients=_catalog_meal_ingredients(catalog_meal),
    )


def _catalog_meal_ingredients(
    catalog_meal: CatalogMeal,
) -> list[MealRecommendationIngredientResponse]:
    return catalog_meal_ingredients(catalog_meal)
