"""Structured AI adapter for non-mutating weekly plan proposals."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanAdjustmentProposal,
    WeeklyMealPlanSlotAdjustment,
)
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort


class WeeklyMealPlanAdjustmentResponse(BaseModel):
    """Bounded provider response; recipe IDs are validated by the app layer."""

    explanation: str = Field(min_length=1, max_length=500)
    slot_changes: list[WeeklyMealPlanSlotAdjustmentResponse] = Field(
        default_factory=list, max_length=14
    )


class WeeklyMealPlanSlotAdjustmentResponse(BaseModel):
    day_index: int = Field(ge=0, le=6)
    slot_index: int = Field(ge=0, le=1)
    action: str = Field(pattern="^(replace|clear)$")
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
    ) -> WeeklyMealPlanAdjustmentProposal:
        context = {
            "current_slots": [
                {
                    "day_index": slot.day_index,
                    "slot_index": slot.slot_index,
                    "recipe_id": slot.recipe_id,
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
                }
                for meal in meals[:200]
            ],
        }
        result = await self.generation_service.generate_meal_plan_async(
            prompt=f"User request: {prompt}\nPlan context: {context}",
            system_message=(
                "You propose changes to a weekly meal plan. Return only structured "
                "slot changes using IDs from available_recipes. Never change logged "
                "slots, invent recipes, or claim allergy safety."
            ),
            response_type="json",
            max_tokens=1200,
            schema=WeeklyMealPlanAdjustmentResponse,
            model_purpose="general",
        )
        parsed = WeeklyMealPlanAdjustmentResponse.model_validate(result)
        return WeeklyMealPlanAdjustmentProposal(
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
