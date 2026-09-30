"""Recipe list/detail projections backed by the curated catalog."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.app.services.catalog_recipe_micronutrient_enrichment_service import (
    CatalogRecipeMicronutrientEnrichmentService,
    FdcMicronutrientLoader,
    MicronutrientEstimator,
)
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.services.weekly_meal_planner.allergen_constraint import (
    recipe_excluded_by_allergen,
    resolve_allergen_preferences,
)
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    is_non_meal_title,
)


@dataclass(frozen=True)
class RecipePage:
    items: tuple[CatalogMeal, ...]
    total: int


class WeeklyRecipeService:
    """Apply bounded, case-insensitive read filters to catalog projections."""

    def __init__(
        self,
        uow_factory,
        *,
        micronutrient_enrichment: CatalogRecipeMicronutrientEnrichmentService
        | None = None,
        micronutrient_estimator: MicronutrientEstimator | None = None,
        fdc_micronutrient_loader: FdcMicronutrientLoader | None = None,
    ):
        self.uow_factory = uow_factory
        self.micronutrient_enrichment = (
            micronutrient_enrichment
            or CatalogRecipeMicronutrientEnrichmentService(
                uow_factory,
                estimator=micronutrient_estimator,
                fdc_loader=fdc_micronutrient_loader,
            )
        )

    async def list(
        self,
        *,
        query=None,
        diet=None,
        max_cook_time=None,
        cuisine=None,
        meal_type=None,
        dislikes=(),
        allergies=(),
        limit=20,
        offset=0,
    ) -> RecipePage:
        async with self.uow_factory() as uow:
            meals = await uow.catalog_recipes.list_active_meals(
                cuisine=cuisine,
                meal_type=meal_type,
            )
            known_allergen_codes = (
                await uow.catalog_recipes.list_allergen_codes() if allergies else ()
            )
        allergen_codes = resolve_allergen_preferences(
            allergies,
            known_allergen_codes,
        )
        if allergen_codes is None:
            return RecipePage(items=(), total=0)
        filtered = [
            meal
            for meal in meals
            if _matches_query(meal, query)
            and _matches_diet(meal, diet)
            and _matches_time(meal, max_cook_time)
            and not _contains_terms(meal, dislikes)
            and not (
                meal_type in {"lunch", "dinner"}
                and is_non_meal_title(meal.name, meal.tag)
            )
            and not recipe_excluded_by_allergen(meal.allergen_codes, allergen_codes)
        ]
        ordered = sorted(
            filtered,
            key=lambda item: (
                item.popularity_rank is None,
                item.popularity_rank or 0,
                item.name.casefold(),
                item.id,
            ),
        )
        return RecipePage(
            items=tuple(ordered[offset : offset + limit]), total=len(ordered)
        )

    async def detail(
        self, recipe_id: str, *, include_cached_micronutrients: bool = True
    ) -> CatalogMeal | None:
        async with self.uow_factory() as uow:
            meal = await uow.catalog_recipes.get_meal_detail(recipe_id)
            if meal is None:
                return None
        if not include_cached_micronutrients:
            return meal
        return await self.micronutrient_enrichment.load_cached(meal)

    async def summaries(self, recipe_ids: Iterable[str]) -> tuple[CatalogMeal, ...]:
        ids = tuple(dict.fromkeys(recipe_id for recipe_id in recipe_ids if recipe_id))
        if not ids:
            return ()
        async with self.uow_factory() as uow:
            meals = await uow.catalog_recipes.get_meals(ids)
        return tuple(meals)


def _matches_query(meal: CatalogMeal, query: str | None) -> bool:
    needle = (query or "").strip().casefold()
    if not needle:
        return True
    haystack = " ".join(
        [
            meal.name,
            meal.cuisine,
            meal.description or "",
            *(item.name for item in meal.ingredients),
        ]
    ).casefold()
    return needle in haystack


def _matches_diet(meal: CatalogMeal, diet: str | None) -> bool:
    if not diet or diet == "any":
        return True
    haystack = " ".join(
        [meal.name, meal.description or "", *(item.name for item in meal.ingredients)]
    ).casefold()
    if diet == "vegetarian":
        return not any(
            term in haystack
            for term in (
                "beef",
                "chicken",
                "pork",
                "fish",
                "shrimp",
                "meat",
                "thịt",
                "gà",
                "bò",
                "cá",
            )
        )
    if diet == "no-pork":
        return not any(
            term in haystack
            for term in ("pork", "ham", "bacon", "sausage", "thịt heo", "thịt lợn")
        )
    return False


def _matches_time(meal: CatalogMeal, max_cook_time: int | None) -> bool:
    if max_cook_time is None:
        return True
    total = int(meal.prep_time_minutes or 0) + int(meal.cook_time_minutes or 0)
    return total <= max_cook_time


def _contains_terms(meal: CatalogMeal, terms: tuple[str, ...]) -> bool:
    if not terms:
        return False
    haystack = " ".join(
        [meal.name, meal.description or "", *(item.name for item in meal.ingredients)]
    ).casefold()
    return any(term.strip().casefold() in haystack for term in terms if term.strip())
