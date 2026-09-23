"""Recipe list/detail projections backed by the curated catalog."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.model.meal_recommendation import CatalogMeal


@dataclass(frozen=True)
class RecipePage:
    items: tuple[CatalogMeal, ...]
    total: int


class WeeklyRecipeService:
    """Apply bounded, case-insensitive read filters to catalog projections."""

    def __init__(self, uow_factory):
        self.uow_factory = uow_factory

    async def list(
        self,
        *,
        query=None,
        diet=None,
        max_cook_time=None,
        cuisine=None,
        dislikes=(),
        allergies=(),
        limit=20,
        offset=0,
    ) -> RecipePage:
        async with self.uow_factory() as uow:
            meals = await uow.catalog_recipes.list_active_meals(cuisine=cuisine)
        filtered = [
            meal
            for meal in meals
            if _matches_query(meal, query)
            and _matches_diet(meal, diet)
            and _matches_time(meal, max_cook_time)
            and not _contains_terms(meal, dislikes)
        ]
        # Allergy data is intentionally disclosure-only until its canonical source is approved.
        del allergies
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

    async def detail(self, recipe_id: str) -> CatalogMeal | None:
        async with self.uow_factory() as uow:
            return await uow.catalog_recipes.get_meal_detail(recipe_id)


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
