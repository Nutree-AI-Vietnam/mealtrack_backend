"""Snapshot-scoped canonical ingredient statistics for catalog ranking."""

from __future__ import annotations

from dataclasses import dataclass
from math import log

from src.domain.model.meal_recommendation import CatalogMeal


@dataclass(frozen=True)
class CatalogIngredientStatistics:
    """Immutable IDF values derived from one catalog snapshot."""

    catalog_size: int
    idf_by_food_reference_id: dict[int, float]

    def idf(self, food_reference_id: int) -> float:
        return self.idf_by_food_reference_id.get(food_reference_id, 0.0)


class CatalogIngredientStatisticsService:
    """Build document-frequency statistics from active catalog meals."""

    def build(
        self,
        catalog_meals: list[CatalogMeal] | tuple[CatalogMeal, ...],
    ) -> CatalogIngredientStatistics:
        catalog_size = len(catalog_meals)
        document_frequency: dict[int, int] = {}
        for meal in sorted(catalog_meals, key=lambda item: item.id):
            seen = {
                ingredient.food_reference_id
                for ingredient in meal.ingredients
                if (
                    ingredient.food_reference_id is not None
                    and ingredient.food_reference_id > 0
                )
            }
            for food_reference_id in seen:
                document_frequency[food_reference_id] = (
                    document_frequency.get(food_reference_id, 0) + 1
                )

        return CatalogIngredientStatistics(
            catalog_size=catalog_size,
            idf_by_food_reference_id={
                food_reference_id: log((catalog_size + 1) / (df + 1)) + 1
                for food_reference_id, df in sorted(document_frequency.items())
            },
        )


EMPTY_CATALOG_INGREDIENT_STATISTICS = CatalogIngredientStatistics(
    catalog_size=0,
    idf_by_food_reference_id={},
)
