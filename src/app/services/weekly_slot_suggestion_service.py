"""Catalog recipes that could replace one weekly plan slot."""

from __future__ import annotations

from typing import Any

from src.api.exceptions import ResourceNotFoundException
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import meal_type_for_slot
from src.domain.services.weekly_meal_planner import WeeklyPlanGenerationService

# Pages read to fill one answer; each page drops the week's recipes and the
# planner's own rejections, so a short answer means the catalog ran out.
_MAX_PAGES = 3
_MAX_PAGE_SIZE = 60


class WeeklySlotSuggestionService:
    """List recipes in GET /v1/recipes order that a PATCH would accept here.

    The recipe list query applies the browse filters (meal type, diets,
    cooking time, protein, the plan's dislikes and allergies) and the order.
    The planner's own hard eligibility then drops anything PATCH would
    reject, so every suggestion can be put in the slot as returned.
    """

    def __init__(
        self,
        uow_factory: Any,
        *,
        generator: WeeklyPlanGenerationService | None = None,
    ):
        self.uow_factory = uow_factory
        self.generator = generator or WeeklyPlanGenerationService()

    async def suggestions(
        self,
        *,
        user_id: str,
        plan_id: str,
        slot_id: str,
        diet: str | None = None,
        max_cook_time: int | None = None,
        min_protein: float | None = None,
        limit: int = 10,
    ) -> tuple[CatalogMeal, ...]:
        async with self.uow_factory() as uow:
            plan = await uow.weekly_meal_plans.get_by_id(
                user_id=user_id, plan_id=plan_id
            )
            if plan is None:
                raise ResourceNotFoundException(
                    "Weekly meal plan not found", error_code="WEEKLY_PLAN_NOT_FOUND"
                )
            slot = next((item for item in plan.slots if item.id == slot_id), None)
            if slot is None:
                raise ResourceNotFoundException(
                    "Weekly meal slot not found", error_code="WEEKLY_SLOT_NOT_FOUND"
                )
            preferences = plan.preferences
            plan_diet = None if preferences.diet == "any" else preferences.diet
            week_recipe_ids = {item.recipe_id for item in plan.slots if item.recipe_id}
            page_size = min(_MAX_PAGE_SIZE, max(limit * 3, 20) + len(week_recipe_ids))
            chosen: list[CatalogMeal] = []
            offset = 0
            for _ in range(_MAX_PAGES):
                page = await uow.catalog_recipes.list_recipe_page(
                    diet=diet or plan_diet,
                    max_cook_time=max_cook_time,
                    meal_type=meal_type_for_slot(slot.slot_index),
                    dislikes=preferences.dislikes,
                    allergies=preferences.allergies,
                    min_protein=min_protein,
                    limit=page_size,
                    offset=offset,
                )
                candidates = [
                    meal
                    for meal in page.items
                    if meal.id not in week_recipe_ids
                    and meal.diet_labels.allows(plan_diet)
                    and meal.diet_labels.allows(diet)
                ]
                # Planner eligibility reads ingredients, which summaries drop.
                sources = {
                    meal.id: meal
                    for meal in await uow.catalog_recipes.get_meals(
                        [meal.id for meal in candidates]
                    )
                }
                for meal in candidates:
                    source = sources.get(meal.id)
                    if (
                        source is not None
                        and self.generator.supports_slot(source, slot.slot_index)
                        and self.generator.is_hard_eligible(source, preferences)
                    ):
                        chosen.append(meal)
                        if len(chosen) == limit:
                            return tuple(chosen)
                offset += page_size
                if offset >= page.total:
                    break
        return tuple(chosen)
