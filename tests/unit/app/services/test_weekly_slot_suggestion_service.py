"""Slot suggestions reuse the recipe list query and the planner's eligibility."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from src.api.exceptions import ResourceNotFoundException
from src.app.services.weekly_slot_suggestion_service import (
    WeeklySlotSuggestionService,
)
from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.meal_recommendation.recipe_diet_labels import (
    RecipeDietLabels,
)
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.domain.ports.catalog_recipe_repository_port import CatalogRecipePage


def _meal(recipe_id, *ingredients, meal_types=("lunch",), allergen_codes=("soy",)):
    return CatalogMeal(
        id=recipe_id,
        catalog_key=recipe_id,
        content_hash="a" * 64,
        name=recipe_id.title(),
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("20"),
        carbs_g=Decimal("30"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("3"),
        meal_types=meal_types,
        allergen_codes=allergen_codes,
        ingredients=tuple(
            CatalogMealIngredient(
                food_reference_id=None,
                display_name=item,
                quantity=Decimal("100"),
                unit="g",
            )
            for item in ingredients
        ),
    )


def _summary(meal):
    return replace(meal, ingredients=(), browse_labels=meal.diet_labels)


def _plan(preferences=None, lunch_recipe="in-week"):
    return WeeklyMealPlan(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        status=WeeklyMealPlanStatus.DRAFT,
        people=1,
        preferences=preferences or WeeklyMealPlanPreferences(),
        timezone="UTC",
        slots=tuple(
            WeeklyMealPlanSlot(
                id=f"slot-{day}-{index}",
                day_index=day,
                slot_index=index,
                recipe_id=lunch_recipe if index == 1 else None,
            )
            for day in range(7)
            for index in range(3)
        ),
    )


class _Catalog:
    def __init__(self, meals):
        self.meals = {meal.id: meal for meal in meals}
        self.order = [meal.id for meal in meals]
        self.page_calls = []
        self.get_meals_calls = []

    async def list_recipe_page(self, **kwargs):
        self.page_calls.append(kwargs)
        ids = [
            recipe_id
            for recipe_id in self.order
            if self.meals[recipe_id].diet_labels.allows(kwargs["diet"])
        ]
        window = ids[kwargs["offset"] : kwargs["offset"] + kwargs["limit"]]
        return CatalogRecipePage(
            tuple(_summary(self.meals[recipe_id]) for recipe_id in window), len(ids)
        )

    async def get_meals(self, ids):
        self.get_meals_calls.append(list(ids))
        return [self.meals[recipe_id] for recipe_id in ids]


class _Plans:
    def __init__(self, plan):
        self.plan = plan

    async def get_by_id(self, *, user_id, plan_id):
        if (user_id, plan_id) == (self.plan.user_id, self.plan.id):
            return self.plan
        return None


def _service(plan, meals):
    catalog = _Catalog(meals)

    class _Uow:
        catalog_recipes = catalog
        weekly_meal_plans = _Plans(plan)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    return WeeklySlotSuggestionService(_Uow), catalog


@pytest.mark.asyncio
async def test_suggestions_keep_list_order_and_skip_the_weeks_recipes():
    meals = [
        _meal("in-week", "rice"),
        _meal("pho", "noodles"),
        _meal("dessert-only", "sugar", meal_types=("snack",)),
        _meal("bun", "noodles"),
    ]
    service, catalog = _service(_plan(), meals)

    result = await service.suggestions(
        user_id="user-1",
        plan_id="plan-1",
        slot_id="slot-2-1",
        max_cook_time=30,
        min_protein=12,
        limit=5,
    )

    assert [meal.id for meal in result] == ["pho", "bun"]
    call = catalog.page_calls[0]
    assert (call["meal_type"], call["max_cook_time"], call["min_protein"]) == (
        "lunch",
        30,
        12,
    )
    assert call["diet"] is None and call["offset"] == 0


@pytest.mark.asyncio
async def test_plan_preferences_apply_through_the_planner_rule():
    preferences = WeeklyMealPlanPreferences(
        diet="vegetarian", dislikes=("tofu",), allergies=("peanut",)
    )
    meals = [
        _meal("prawn-rice", "rice", "prawn"),  # planner meat word, browse misses it
        _meal("tofu-bowl", "tofu"),
        _meal("satay", "rice", allergen_codes=("peanut",)),
        _meal("undisclosed", "rice", allergen_codes=()),
        _meal("veg-rice", "rice", "carrot"),
    ]
    service, catalog = _service(_plan(preferences), meals)

    result = await service.suggestions(
        user_id="user-1", plan_id="plan-1", slot_id="slot-0-1"
    )

    assert [meal.id for meal in result] == ["veg-rice"]
    call = catalog.page_calls[0]
    assert call["diet"] == "vegetarian"
    assert call["dislikes"] == ("tofu",) and call["allergies"] == ("peanut",)


@pytest.mark.asyncio
async def test_query_diet_narrows_but_never_widens_the_plan_diet():
    preferences = WeeklyMealPlanPreferences(diet="no-pork")
    meals = [
        _meal("chicken", "chicken"),
        _meal("ham", "bread", "ham"),
        _meal("tofu", "tofu"),
    ]
    service, catalog = _service(_plan(preferences), meals)

    vegetarian = await service.suggestions(
        user_id="user-1", plan_id="plan-1", slot_id="slot-0-1", diet="vegetarian"
    )
    plan_only = await service.suggestions(
        user_id="user-1", plan_id="plan-1", slot_id="slot-0-1"
    )

    # "ham" passes the browse vegetarian words but not the plan's no-pork.
    assert [meal.id for meal in vegetarian] == ["tofu"]
    assert [meal.id for meal in plan_only] == ["chicken", "tofu"]
    assert [call["diet"] for call in catalog.page_calls] == ["vegetarian", "no-pork"]
    assert vegetarian[0].diet_labels == RecipeDietLabels(True, True)


@pytest.mark.asyncio
async def test_pages_until_the_limit_is_filled():
    meals = [_meal("in-week", "rice")] + [
        _meal(f"r{index}", "rice") for index in range(70)
    ]
    service, catalog = _service(_plan(), meals)

    result = await service.suggestions(
        user_id="user-1", plan_id="plan-1", slot_id="slot-0-1", limit=30
    )

    assert [meal.id for meal in result] == [f"r{index}" for index in range(30)]
    assert len(catalog.page_calls) == 1
    assert catalog.page_calls[0]["limit"] == 60

    exhausted = await service.suggestions(
        user_id="user-1", plan_id="plan-1", slot_id="slot-0-0", limit=30
    )
    assert exhausted == ()  # lunch-only catalog has nothing for breakfast


@pytest.mark.asyncio
async def test_unknown_plan_or_slot_is_404():
    service, _ = _service(_plan(), [])

    with pytest.raises(ResourceNotFoundException) as plan_error:
        await service.suggestions(user_id="other", plan_id="plan-1", slot_id="slot-0-1")
    with pytest.raises(ResourceNotFoundException) as slot_error:
        await service.suggestions(user_id="user-1", plan_id="plan-1", slot_id="nope")

    assert plan_error.value.error_code == "WEEKLY_PLAN_NOT_FOUND"
    assert slot_error.value.error_code == "WEEKLY_SLOT_NOT_FOUND"
