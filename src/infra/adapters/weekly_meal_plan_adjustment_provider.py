"""Structured AI adapter for non-mutating weekly plan proposals."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanAdjustmentProposal,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlotAdjustment,
)
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort


class WeeklyMealPlanAdjustmentResponse(BaseModel):
    """Bounded provider response; recipe IDs are validated by the app layer."""

    explanation: str = Field(min_length=1, max_length=1000)
    slot_changes: list[WeeklyMealPlanSlotAdjustmentResponse] = Field(
        default_factory=list, max_length=14
    )


class WeeklyMealPlanSlotAdjustmentResponse(BaseModel):
    day_index: int = Field(ge=0, le=6)
    slot_index: int = Field(ge=0, le=1)
    action: Literal["replace", "clear"]
    new_recipe_id: str | None = Field(default=None, max_length=36)


WeeklyMealPlanAdjustmentResponse.model_rebuild()


class StructuredWeeklyMealPlanAdjustmentProvider:
    """Use the existing fallback-aware generation service for proposals."""

    def __init__(self, generation_service: MealGenerationServicePort):
        self.generation_service = generation_service

    async def propose(
        self,
        *,
        prompt: str,
        plan: WeeklyMealPlan,
        meals: tuple[CatalogMeal, ...],
        preferences: WeeklyMealPlanPreferences | None = None,
        profile_dietary_preferences: tuple[str, ...] = (),
        target_day_index: int | None = None,
        target_slot_index: int | None = None,
    ) -> WeeklyMealPlanAdjustmentProposal:
        preferences = preferences or plan.preferences
        meal_by_id = {meal.id: meal for meal in meals}
        target = None
        if target_day_index is not None and target_slot_index is not None:
            current = next(
                slot
                for slot in plan.slots
                if slot.day_index == target_day_index
                and slot.slot_index == target_slot_index
            )
            target_meal = (
                meal_by_id.get(current.recipe_id)
                if current.recipe_id is not None
                else None
            )
            target = {
                "day_index": target_day_index,
                "slot_index": target_slot_index,
                "meal_type": "lunch" if target_slot_index == 0 else "dinner",
                "current_recipe_id": current.recipe_id,
                "current_recipe_name": target_meal.name if target_meal else None,
            }
        context = {
            "base_revision": plan.revision,
            "week_start_date": plan.week_start_date.isoformat(),
            "scope": "meal" if target is not None else "week",
            "target_slot": target,
            "saved_preferences": preferences.to_dict(),
            "profile_dietary_preferences": list(profile_dietary_preferences),
            "current_slots": [
                {
                    "day_index": slot.day_index,
                    "slot_index": slot.slot_index,
                    "recipe_id": slot.recipe_id,
                    "recipe_name": (
                        meal_by_id[slot.recipe_id].name
                        if slot.recipe_id in meal_by_id
                        else None
                    ),
                    "is_logged": slot.is_logged,
                }
                for slot in plan.slots
            ],
            "available_recipes": [
                {
                    "id": meal.id,
                    "name": meal.name,
                    "cuisine": meal.cuisine,
                    "meal_types": meal.meal_types,
                    "cook_time_minutes": meal.cook_time_minutes,
                    "tag": meal.tag,
                    "allergen_codes": meal.allergen_codes,
                    "protein_g": float(meal.protein_g),
                    "calories": meal.calories,
                }
                for meal in meals[:200]
            ],
        }
        result = await self.generation_service.generate_meal_plan_async(
            prompt=(
                f"User request: {prompt}\nPlan context JSON: "
                f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
            ),
            system_message=(
                "You propose reviewable changes to a weekly meal plan. Return only "
                "structured slot changes using IDs from available_recipes. Respect "
                "diet, allergy, and dislike preferences as strict constraints; "
                "use saved profile dietary preferences as ranking hints unless they "
                "are represented by a strict plan preference. The current request "
                "may narrow a preference but must not override saved allergies. "
                "Honor cuisine and cooking-time preferences when eligible recipes "
                "are available. "
                "Never change logged slots, invent recipes, or claim allergy safety. "
                "When scope is meal, replace only target_slot with a different "
                "eligible recipe; never return the current recipe, clear the slot, "
                "or return no change when a safe replacement exists. When scope is "
                "week, change only slots requested by the user. Each action must be exactly "
                "replace or clear; replace requires new_recipe_id and clear requires "
                "new_recipe_id to be null. Keep explanation to one concise sentence "
                "under 160 characters; do not list individual slot changes there."
            ),
            response_type="json",
            max_tokens=3000,
            schema=WeeklyMealPlanAdjustmentResponse,
            model_purpose="general",
        )
        parsed = WeeklyMealPlanAdjustmentResponse.model_validate(result)
        return WeeklyMealPlanAdjustmentProposal(
            base_revision=plan.revision,
            explanation=parsed.explanation,
            slot_changes=tuple(
                WeeklyMealPlanSlotAdjustment(
                    day_index=change.day_index,
                    slot_index=change.slot_index,
                    action=change.action,
                    new_recipe_id=change.new_recipe_id,
                )
                for change in parsed.slot_changes
            ),
        )
