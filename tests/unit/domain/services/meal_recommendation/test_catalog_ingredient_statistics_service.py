from decimal import Decimal
from math import log

import pytest

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.services.meal_recommendation.catalog_ingredient_statistics_service import (
    CatalogIngredientStatisticsService,
)


def test_idf_counts_each_canonical_ingredient_once_per_meal():
    stats = CatalogIngredientStatisticsService().build(
        [
            _meal("meal-1", (1, 1, 2)),
            _meal("meal-2", (1, 3)),
            _meal("meal-3", (3, 0, -1)),
        ]
    )

    assert stats.catalog_size == 3
    assert stats.idf_by_food_reference_id[1] == pytest.approx(
        log((3 + 1) / (2 + 1)) + 1
    )
    assert stats.idf_by_food_reference_id[2] == pytest.approx(
        log((3 + 1) / (1 + 1)) + 1
    )
    assert stats.idf_by_food_reference_id[3] == pytest.approx(
        log((3 + 1) / (2 + 1)) + 1
    )
    assert 0 not in stats.idf_by_food_reference_id


def test_idf_is_order_independent_and_empty_safe():
    service = CatalogIngredientStatisticsService()
    meals = [_meal("b", (2, 3)), _meal("a", (1, 2))]

    first = service.build(meals)
    second = service.build(list(reversed(meals)))

    assert first == second
    assert service.build([]).catalog_size == 0
    assert service.build([]).idf_by_food_reference_id == {}


def test_idf_ignores_unmapped_and_non_positive_food_references():
    stats = CatalogIngredientStatisticsService().build(
        [_meal("meal-1", (None, 0, -1, 7))]
    )

    assert stats.catalog_size == 1
    assert stats.idf_by_food_reference_id == {7: pytest.approx(1.0)}


def _meal(meal_id: str, food_reference_ids: tuple[int | None, ...]) -> CatalogMeal:
    return CatalogMeal(
        id=meal_id,
        catalog_key=f"key-{meal_id}",
        content_hash=f"{meal_id:0<64}"[:64],
        name=f"Meal {meal_id}",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal("20"),
        carbs_g=Decimal("40"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=("breakfast",),
        ingredients=tuple(
            CatalogMealIngredient(
                food_reference_id=food_reference_id,
                display_name="Ingredient",
                quantity=Decimal("100"),
                unit="g",
            )
            for food_reference_id in food_reference_ids
        ),
    )
