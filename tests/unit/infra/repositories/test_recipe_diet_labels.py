"""One browse diet rule behind the recipe filter, its columns and labels."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.meal_recommendation.recipe_diet_labels import (
    RecipeDietLabels,
    recipe_browse_text,
)
from src.infra.repositories.catalog_projection_filters import recipe_filters
from src.infra.repositories.catalog_projection_legacy_page import legacy_recipe_page
from src.infra.repositories.catalog_projection_mapper import projection_to_meal


def _meal(recipe_id, name, *ingredients, protein="20", description=None):
    return CatalogMeal(
        id=recipe_id,
        catalog_key=recipe_id,
        content_hash="a" * 64,
        name=name,
        cuisine="vietnamese",
        description=description,
        image_url=None,
        protein_g=Decimal(protein),
        carbs_g=Decimal("30"),
        fat_g=Decimal("10"),
        fiber_g=Decimal("3"),
        meal_types=("lunch",),
        ingredients=tuple(
            CatalogMealIngredient(
                food_reference_id=None,
                display_name=item,
                quantity=Decimal("100"),
                unit="g",
            )
            for item in ingredients
        ),
    )


@pytest.mark.parametrize(
    ("name", "ingredients", "expected"),
    [
        ("Đậu hũ sốt cà", ("đậu hũ", "cà chua"), (True, True)),
        ("Cơm gà", ("gạo", "gà"), (False, True)),
        ("Bún thịt heo", ("bún", "thịt heo"), (False, False)),
        # The browse word list has no "ham" in the meat words.
        ("Ham sandwich", ("bread", "ham"), (True, False)),
    ],
)
def test_labels_follow_the_browse_word_lists(name, ingredients, expected):
    labels = RecipeDietLabels.for_recipe(name, None, ingredients)

    assert (labels.is_vegetarian, labels.is_pork_free) == expected
    assert _meal("r", name, *ingredients).diet_labels == labels


def test_allows_matches_the_diet_filter_values():
    labels = RecipeDietLabels(is_vegetarian=False, is_pork_free=True)

    assert labels.allows(None) and labels.allows("any")
    assert labels.allows("no-pork") and not labels.allows("vegetarian")
    assert not labels.allows("keto")


def test_stored_labels_win_over_text_without_ingredients():
    meal = replace(
        _meal("r", "Cơm chiên"),
        browse_labels=RecipeDietLabels(is_vegetarian=False, is_pork_free=False),
    )

    assert meal.diet_labels == RecipeDietLabels(False, False)


def test_projection_rows_label_from_the_filter_columns():
    meal = _meal("r", "Rau xào")
    row = SimpleNamespace(
        summary_payload=meal.to_dict(),
        protein_g=meal.protein_g,
        carbs_g=meal.carbs_g,
        fat_g=meal.fat_g,
        fiber_g=meal.fiber_g,
        sugar_g=meal.sugar_g,
        selection_constraint_text="",
        selection_contains_meat=False,
        selection_contains_pork=False,
        non_meal=False,
        browse_vegetarian=False,
        browse_no_pork=True,
    )

    assert projection_to_meal(row).diet_labels == RecipeDietLabels(False, True)


def test_browse_text_is_casefolded_name_description_and_ingredients():
    assert recipe_browse_text("Phở BÒ", "Nước dùng", ["Bánh phở"]) == (
        "phở bò nước dùng bánh phở"
    )


def test_min_protein_is_a_per_serving_projection_filter():
    (clause,) = recipe_filters(min_protein=25)

    sql = str(clause.compile(dialect=postgresql.dialect()))
    assert "protein_g >=" in sql


@pytest.mark.asyncio
async def test_legacy_page_filters_diet_and_protein_and_keeps_labels():
    class _Repository:
        async def list_active_meals(self, *, meal_type=None):
            return [
                _meal("tofu", "Đậu hũ", "đậu hũ", protein="30"),
                _meal("chicken", "Gà nướng", "gà", protein="40"),
                _meal("salad", "Salad", "rau", protein="5"),
            ]

    page = await legacy_recipe_page(
        _Repository(),
        query=None,
        diet="vegetarian",
        max_cook_time=None,
        cuisine=None,
        meal_type=None,
        dislikes=(),
        codes=(),
        limit=10,
        offset=0,
        min_protein=25,
    )

    assert [meal.id for meal in page.items] == ["tofu"]
    assert page.items[0].browse_labels == RecipeDietLabels(True, True)
