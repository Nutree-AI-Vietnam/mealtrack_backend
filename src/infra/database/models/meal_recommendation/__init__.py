"""Meal recommendation catalog database models."""

from src.infra.database.models.meal_recommendation.catalog_preparation import (
    CatalogPreparationJobORM,
    CatalogRecipeTranslationORM,
)
from src.infra.database.models.meal_recommendation.catalog_projection import (
    CatalogPublicationVersionORM,
    MealCatalogProjectionAllergenORM,
    MealCatalogProjectionORM,
)

from .catalog_allergens import AllergenReferenceORM, MealCatalogAllergenORM
from .catalog_micronutrient_enrichment import (
    MealCatalogMicronutrientEnrichmentORM,
)
from .catalog_recipe import (
    MealCatalogIngredientORM,
    MealCatalogORM,
    MealCatalogStepORM,
)
from .meal_recommendation_plan import (
    MealRecommendationOperationORM,
    MealRecommendationORM,
)

__all__ = [
    "CatalogPreparationJobORM",
    "CatalogRecipeTranslationORM",
    "CatalogPublicationVersionORM",
    "MealCatalogProjectionORM",
    "MealCatalogProjectionAllergenORM",
    "AllergenReferenceORM",
    "MealCatalogAllergenORM",
    "MealCatalogIngredientORM",
    "MealCatalogORM",
    "MealCatalogStepORM",
    "MealCatalogMicronutrientEnrichmentORM",
    "MealRecommendationORM",
    "MealRecommendationOperationORM",
]
