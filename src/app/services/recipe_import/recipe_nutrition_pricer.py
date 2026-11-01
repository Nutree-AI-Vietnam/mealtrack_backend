"""Price an ingredient list with the backend nutrition lookup.

The AI never decides calories: each line is weighed (exact for weights and
liquid volumes, estimated otherwise), then the shared three-tier lookup
(food reference, FatSecret, AI estimate) supplies macros per gram.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.domain.model.nutrition.macros import Macros
from src.domain.model.recipe_import.enums import IngredientNutritionSource
from src.domain.model.recipe_import.extracted_recipe import ExtractedIngredient
from src.domain.model.recipe_import.imported_recipe import (
    ImportedIngredient,
    RecipeMacros,
    RecipeNutrition,
)
from src.domain.model.recipe_import.ingredient_weight import ingredient_grams
from src.domain.model.recipe_import.limits import MAX_SERVINGS
from src.domain.ports.recipe_import_ports import IngredientNutritionPort
from src.domain.services.meal_suggestion.nutrition_lookup_service import (
    IngredientMacros,
)

_SOURCES = {
    "T1_food_reference": IngredientNutritionSource.FOOD_REFERENCE,
    "T2_fatsecret": IngredientNutritionSource.FATSECRET,
    "T3_ai_estimate": IngredientNutritionSource.AI_ESTIMATE,
}
# Used only when neither the source nor the AI gave a serving count.
_FALLBACK_KCAL_PER_SERVING = 550.0
_FALLBACK_MAX_SERVINGS = 12


@dataclass(frozen=True, slots=True)
class PricedRecipe:
    ingredients: tuple[ImportedIngredient, ...]
    nutrition: RecipeNutrition
    servings_estimated: bool


class RecipeNutritionPricer:
    def __init__(self, nutrition: IngredientNutritionPort) -> None:
        self._nutrition = nutrition

    async def price(
        self, ingredients: Sequence[ExtractedIngredient], *, servings: int | None
    ) -> PricedRecipe:
        weighed = [
            (
                item,
                ingredient_grams(
                    english_name=item.english_name,
                    quantity=item.quantity,
                    unit=item.unit,
                    estimated_grams=item.grams,
                ),
            )
            for item in ingredients
        ]
        lines = [
            {"name": item.english_name, "amount": grams, "unit": "g"}
            for item, grams in weighed
            if grams is not None
        ]
        priced: list[IngredientMacros] = []
        if lines:
            meal = await self._nutrition.calculate_meal_macros(lines)
            priced = list(meal.ingredients)
        priced_iter = iter(priced)
        imported: list[ImportedIngredient] = []
        for item, grams in weighed:
            macros = next(priced_iter, None) if grams is not None else None
            imported.append(_imported(item, grams, macros))
        total = _sum(imported)
        estimated_servings = servings is None
        count = servings or _fallback_servings(total.calories)
        return PricedRecipe(
            ingredients=tuple(imported),
            nutrition=RecipeNutrition(
                servings=count,
                total=total,
                per_serving=_divide(total, count),
                unpriced_ingredients=tuple(
                    item.name
                    for item in imported
                    if not item.optional and item.macros is None
                ),
                estimated=any(
                    item.nutrition_source == IngredientNutritionSource.AI_ESTIMATE
                    for item in imported
                ),
            ),
            servings_estimated=estimated_servings,
        )


def _imported(
    item: ExtractedIngredient, grams: float | None, macros: IngredientMacros | None
) -> ImportedIngredient:
    return ImportedIngredient(
        name=item.name,
        english_name=item.english_name,
        quantity=item.quantity,
        unit=item.unit,
        grams=grams,
        note=item.note,
        optional=item.optional,
        group=item.group,
        macros=(
            RecipeMacros(
                calories=macros.calories,
                protein=macros.protein,
                carbs=macros.carbs,
                fat=macros.fat,
                fiber=macros.fiber,
                sugar=macros.sugar,
            )
            if macros is not None
            else None
        ),
        food_reference_id=macros.food_reference_id if macros is not None else None,
        nutrition_source=_SOURCES.get(macros.source_tier)
        if macros is not None
        else None,
    )


def _sum(items: Sequence[ImportedIngredient]) -> RecipeMacros:
    priced = [item.macros for item in items if item.macros is not None]
    protein = sum(m.protein for m in priced)
    carbs = sum(m.carbs for m in priced)
    fat = sum(m.fat for m in priced)
    fiber = sum(m.fiber for m in priced)
    sugar = sum(m.sugar for m in priced)
    return _macros(protein, carbs, fat, fiber, sugar)


def _divide(total: RecipeMacros, servings: int) -> RecipeMacros:
    return _macros(
        total.protein / servings,
        total.carbs / servings,
        total.fat / servings,
        total.fiber / servings,
        total.sugar / servings,
    )


def _macros(
    protein: float, carbs: float, fat: float, fiber: float, sugar: float
) -> RecipeMacros:
    # Calories always come from the backend formula, never from the sources.
    calories = Macros.raw_total_calories(protein, carbs, fat, fiber)
    return RecipeMacros(
        calories=round(calories, 1),
        protein=round(protein, 1),
        carbs=round(carbs, 1),
        fat=round(fat, 1),
        fiber=round(fiber, 1),
        sugar=round(sugar, 1),
    )


def _fallback_servings(total_calories: float) -> int:
    if total_calories <= 0:
        return 1
    estimate = round(total_calories / _FALLBACK_KCAL_PER_SERVING)
    return max(1, min(estimate, _FALLBACK_MAX_SERVINGS, MAX_SERVINGS))
