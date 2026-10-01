"""Bounded ingredient-diversity reranking for catalog meal plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.services.meal_recommendation.catalog_ingredient_statistics_service import (
    CatalogIngredientStatistics,
)
from src.domain.services.meal_recommendation.recipe_scoring_service import RecipeScore

SHORTLIST_LIMIT = 30


class PlanDiversityRerankingService:
    """Apply deterministic diversity scoring to a fixed shortlist."""

    def __init__(
        self,
        diversity_fit: Callable[
            [CatalogMeal, tuple[CatalogMeal, ...], CatalogIngredientStatistics], float
        ]
        | None = None,
    ) -> None:
        self._diversity_fit = diversity_fit or self.diversity_fit

    def weighted_overlap(
        self,
        left: CatalogMeal,
        right: CatalogMeal,
        ingredient_statistics: CatalogIngredientStatistics,
    ) -> float:
        left_ids = _canonical_ids(left)
        right_ids = _canonical_ids(right)
        union = left_ids | right_ids
        if not union:
            return 0.0
        union_weight = sum(ingredient_statistics.idf(food_id) for food_id in union)
        if union_weight <= 0:
            return 0.0
        intersection_weight = sum(
            ingredient_statistics.idf(food_id) for food_id in left_ids & right_ids
        )
        return max(0.0, min(1.0, intersection_weight / union_weight))

    def diversity_fit(
        self,
        candidate: CatalogMeal,
        comparison_meals: tuple[CatalogMeal, ...],
        ingredient_statistics: CatalogIngredientStatistics,
    ) -> float:
        if not comparison_meals:
            return 1.0
        maximum_overlap = max(
            self.weighted_overlap(candidate, meal, ingredient_statistics)
            for meal in comparison_meals
        )
        return max(0.0, min(1.0, 1.0 - maximum_overlap))

    def rerank_shortlist(
        self,
        ranked_pool: list[RecipeScore],
        *,
        comparison_meals: tuple[CatalogMeal, ...],
        ingredient_statistics: CatalogIngredientStatistics,
    ) -> list[RecipeScore]:
        contextual = []
        for item in ranked_pool[:SHORTLIST_LIMIT]:
            diversity_fit = self._diversity_fit(
                item.catalog_meal,
                comparison_meals,
                ingredient_statistics,
            )
            contextual.append(
                replace(
                    item,
                    diversity_fit=diversity_fit,
                    score=item.contextual_score(diversity_fit=diversity_fit),
                )
            )
        return sorted(contextual, key=lambda item: (-item.score, item.catalog_meal.id))


def _canonical_ids(catalog_meal: CatalogMeal) -> set[int]:
    return {
        ingredient.food_reference_id
        for ingredient in catalog_meal.ingredients
        if (
            ingredient.food_reference_id is not None
            and ingredient.food_reference_id > 0
        )
    }
