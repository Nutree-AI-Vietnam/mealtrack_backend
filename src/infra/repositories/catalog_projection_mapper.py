"""Map compact typed rows without ingredients or recipe payload hydration."""

from dataclasses import replace
from decimal import Decimal
from typing import Any, cast

from src.infra.database.models.meal_recommendation.catalog_projection import (
    MealCatalogProjectionORM,
)


def projection_to_meal(row: MealCatalogProjectionORM, *, candidate: bool = False):
    from src.domain.model.meal_recommendation.catalog_recipe import CatalogMeal
    from src.domain.model.meal_recommendation.catalog_selection_features import (
        CatalogSelectionFeatures,
    )

    meal = CatalogMeal.from_dict(cast(dict[str, Any], row.summary_payload))
    return replace(
        meal,
        protein_g=cast(Decimal, row.protein_g),
        carbs_g=cast(Decimal, row.carbs_g),
        fat_g=cast(Decimal, row.fat_g),
        fiber_g=cast(Decimal, row.fiber_g),
        sugar_g=cast(Decimal, row.sugar_g),
        selection_features=CatalogSelectionFeatures(
            cast(str, row.selection_constraint_text),
            cast(bool, row.selection_contains_meat),
            cast(bool, row.selection_contains_pork),
            cast(bool, row.non_meal),
        )
        if candidate
        else None,
    )
