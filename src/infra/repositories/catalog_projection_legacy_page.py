"""Authoritative list fallback while query projections reconcile."""

from src.domain.model.weekly_meal_planner import SLOT_MEAL_TYPES
from src.domain.ports.catalog_recipe_repository_port import CatalogRecipePage


async def legacy_recipe_page(
    repository,
    *,
    query,
    diet,
    max_cook_time,
    cuisine,
    meal_type,
    dislikes,
    codes,
    limit,
    offset,
):
    # Legacy reads remain the authority until every mandatory query field is current.
    from src.domain.services.weekly_meal_planner.allergen_constraint import (
        recipe_excluded_by_allergen,
    )
    from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
        is_non_meal_title,
    )
    from src.infra.repositories.catalog_projection_builder import (
        BROWSE_MEAT_WORDS,
        BROWSE_PORK_WORDS,
    )

    meals = await repository.list_active_meals(meal_type=meal_type)
    filtered = []
    for meal in meals:
        if (
            cuisine
            and cuisine.strip()
            and meal.cuisine.casefold() != cuisine.strip().casefold()
        ):
            continue
        names = [ingredient.name for ingredient in meal.ingredients]
        browse = " ".join([meal.name, meal.description or "", *names]).casefold()
        search = " ".join(
            [meal.name, meal.cuisine, meal.description or "", *names]
        ).casefold()
        if (query or "").strip().casefold() not in search:
            continue
        if diet and diet != "any":
            terms = (
                BROWSE_MEAT_WORDS
                if diet == "vegetarian"
                else BROWSE_PORK_WORDS
                if diet == "no-pork"
                else None
            )
            if terms is None or any(term in browse for term in terms):
                continue
        if (
            max_cook_time is not None
            and int(meal.prep_time_minutes or 0) + int(meal.cook_time_minutes or 0)
            > max_cook_time
        ):
            continue
        if any(term.strip().casefold() in browse for term in dislikes if term.strip()):
            continue
        if meal_type in SLOT_MEAL_TYPES and is_non_meal_title(meal.name, meal.tag):
            continue
        if recipe_excluded_by_allergen(meal.allergen_codes, codes):
            continue
        filtered.append(meal)
    filtered.sort(
        key=lambda meal: (
            meal.popularity_rank is None,
            meal.popularity_rank or 0,
            meal.name.casefold(),
            meal.id,
        )
    )
    return CatalogRecipePage(tuple(filtered[offset : offset + limit]), len(filtered))
