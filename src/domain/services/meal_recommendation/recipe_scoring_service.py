"""Deterministic catalog_meal scoring for meal recommendation candidates."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.services.meal_recommendation.catalog_ingredient_statistics_service import (
    EMPTY_CATALOG_INGREDIENT_STATISTICS,
    CatalogIngredientStatistics,
)
from src.domain.services.meal_recommendation.ingredient_affinity_service import (
    IngredientAffinityProfile,
)


@dataclass(frozen=True)
class RecipeScore:
    """Score plus deterministic tie-break identity."""

    catalog_meal: CatalogMeal
    score: float
    calorie_fit: float = 0.0
    ingredient_fit: float = 0.0
    diversity_fit: float = 0.0
    calorie_weight: float = 0.82
    ingredient_weight: float = 0.18
    diversity_weight: float = 0.0

    def contextual_score(self, *, diversity_fit: float) -> float:
        score = (
            self.calorie_fit * self.calorie_weight
            + self.ingredient_fit * self.ingredient_weight
            + diversity_fit * self.diversity_weight
        )
        return _round_score(score)


class RecipeScoringService:
    """Score immutable catalog_meal versions without learned popularity or randomness."""

    def score(
        self,
        catalog_meal: CatalogMeal,
        *,
        target_calories: int,
        affinity: IngredientAffinityProfile,
        ingredient_statistics: CatalogIngredientStatistics = EMPTY_CATALOG_INGREDIENT_STATISTICS,
        diversity_fit: float = 0.0,
    ) -> RecipeScore:
        if target_calories <= 0:
            raise ValueError("target_calories must be positive")

        calorie_distance = (
            abs(catalog_meal.calories - target_calories) / target_calories
        )
        calorie_fit = max(0.0, 1.0 - min(calorie_distance, 1.0))
        ingredient_fit = _ingredient_cosine(
            catalog_meal, affinity, ingredient_statistics
        )
        ingredient_weight = 0.35 * _bounded(affinity.confidence)
        diversity_weight = 0.10
        calorie_weight = 0.90 - ingredient_weight
        score = _round_score(
            calorie_fit * calorie_weight
            + ingredient_fit * ingredient_weight
            + diversity_fit * diversity_weight
        )
        return RecipeScore(
            catalog_meal=catalog_meal,
            score=score,
            calorie_fit=calorie_fit,
            ingredient_fit=ingredient_fit,
            diversity_fit=diversity_fit,
            calorie_weight=calorie_weight,
            ingredient_weight=ingredient_weight,
            diversity_weight=diversity_weight,
        )

    def rank(
        self,
        catalog_meals: list[CatalogMeal],
        *,
        meal_type: str,
        target_calories: int,
        affinity: IngredientAffinityProfile,
        excluded_catalog_meal_ids: set[str] | None = None,
        ingredient_statistics: CatalogIngredientStatistics = EMPTY_CATALOG_INGREDIENT_STATISTICS,
    ) -> list[RecipeScore]:
        excluded_catalog_meal_ids = excluded_catalog_meal_ids or set()
        scored = [
            self.score(
                catalog_meal,
                target_calories=target_calories,
                affinity=affinity,
                ingredient_statistics=ingredient_statistics,
            )
            for catalog_meal in catalog_meals
            if catalog_meal.id not in excluded_catalog_meal_ids
            and meal_type in catalog_meal.meal_types
            and catalog_meal.calories > 0
        ]
        return sorted(scored, key=lambda item: (-item.score, item.catalog_meal.id))


def _ingredient_cosine(
    catalog_meal: CatalogMeal,
    affinity: IngredientAffinityProfile,
    ingredient_statistics: CatalogIngredientStatistics,
) -> float:
    if not affinity.weights:
        return 0.0
    meal_ids = {
        ingredient.food_reference_id
        for ingredient in catalog_meal.ingredients
        if (
            ingredient.food_reference_id is not None
            and ingredient.food_reference_id > 0
        )
    }
    if not meal_ids:
        return 0.0
    user_norm = 0.0
    meal_norm = 0.0
    dot_product = 0.0
    for food_reference_id, history_weight in affinity.weights.items():
        idf = ingredient_statistics.idf(food_reference_id)
        component = history_weight * idf
        user_norm += component * component
        if food_reference_id in meal_ids:
            dot_product += component * idf
    for food_reference_id in meal_ids:
        idf = ingredient_statistics.idf(food_reference_id)
        meal_norm += idf * idf
    if user_norm <= 0 or meal_norm <= 0:
        return 0.0
    value = dot_product / (sqrt(user_norm) * sqrt(meal_norm))
    if not isfinite(value):
        return 0.0
    return _bounded(value)


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round_score(value: float) -> float:
    return round(value, 6)
