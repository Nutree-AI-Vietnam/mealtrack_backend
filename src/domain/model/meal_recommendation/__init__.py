"""Meal recommendation domain models."""

from .catalog_recipe import (
    CatalogMeal,
    CatalogMealIngredient,
    CatalogMealStep,
    MealRecommendationAlternative,
    MealRecommendationInsufficiency,
    MealRecommendationInsufficiencyReason,
    MealRecommendationPlan,
    MealRecommendationSlot,
)
from .meal_recommendation_plan import (
    PersistedMealRecommendationCandidate,
    PersistedMealRecommendationPlan,
    PersistedMealRecommendationSlot,
    PersistedMealRecommendationSlotMutationResult,
)

__all__ = [
    "CatalogMeal",
    "CatalogMealIngredient",
    "CatalogMealStep",
    "MealRecommendationAlternative",
    "MealRecommendationInsufficiency",
    "MealRecommendationInsufficiencyReason",
    "MealRecommendationPlan",
    "MealRecommendationSlot",
    "PersistedMealRecommendationCandidate",
    "PersistedMealRecommendationPlan",
    "PersistedMealRecommendationSlot",
    "PersistedMealRecommendationSlotMutationResult",
]
