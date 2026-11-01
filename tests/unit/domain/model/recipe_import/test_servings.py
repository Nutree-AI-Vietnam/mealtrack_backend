"""Servings count people; a yield that counts pieces is what a recipe makes."""

import pytest

from src.domain.model.recipe_import import ExtractedRecipe, StructuredRecipe
from src.domain.model.recipe_import.schema_org import find_schema_recipe
from src.domain.model.recipe_import.servings import (
    with_source_servings,
    yield_servings,
)


@pytest.mark.parametrize(
    ("raw", "servings"),
    [
        ("Serves 4", 4),
        ("Serves 4-6", 4),
        (["4", "4 servings"], 4),
        ("4", 4),
        (6, 6),
        ("cho 4 người", 4),
        ("2 phần", 2),
        ("3 نفر", 3),
        ("2人分", 2),
        ("6 porciones", 6),
        ("Makes 12", None),
        (["24", "24 cookies"], None),
        ("1 loaf", None),
        ("8 slices", None),
        ("", None),
        (None, None),
        (True, None),
    ],
)
def test_yield_counts_people_only(raw, servings):
    assert yield_servings(raw) == servings


def _recipe(**values) -> ExtractedRecipe:
    return ExtractedRecipe(is_recipe=True, name="Pancakes", language="vi", **values)


def test_a_piece_yield_undoes_servings_copied_from_it():
    structured = StructuredRecipe(recipe_yield="Makes 12", servings=None)

    recipe = with_source_servings(
        _recipe(servings=12, servings_stated=True), structured
    )

    assert (recipe.servings, recipe.servings_stated, recipe.makes) == (
        None,
        False,
        "Makes 12",
    )


def test_a_piece_yield_keeps_the_ai_people_estimate_and_wording():
    structured = StructuredRecipe(recipe_yield="Makes 12", servings=None)

    recipe = with_source_servings(
        _recipe(servings=4, servings_stated=False, makes="12 bánh"), structured
    )

    assert (recipe.servings, recipe.servings_stated, recipe.makes) == (
        4,
        False,
        "12 bánh",
    )


def test_servings_stated_elsewhere_on_the_page_are_kept():
    structured = StructuredRecipe(recipe_yield="Makes 12", servings=None)

    recipe = with_source_servings(_recipe(servings=4, servings_stated=True), structured)

    assert (recipe.servings, recipe.servings_stated) == (4, True)


def test_a_yield_that_counts_people_is_stated():
    recipe = with_source_servings(
        _recipe(servings=2, servings_stated=False),
        StructuredRecipe(recipe_yield="Serves 6", servings=6),
    )

    assert (recipe.servings, recipe.servings_stated) == (6, True)


def test_without_a_page_yield_the_ai_reading_stands():
    recipe = _recipe(servings=3, servings_stated=True)

    assert with_source_servings(recipe, None) is recipe
    assert with_source_servings(recipe, StructuredRecipe()) == recipe


def test_schema_recipe_with_a_piece_yield_states_no_servings():
    recipe = find_schema_recipe(
        [
            {
                "@type": "Recipe",
                "name": "Easy pancakes",
                "recipeYield": ["12", "12 pancakes"],
            }
        ]
    )

    assert recipe is not None
    assert (recipe.servings, recipe.recipe_yield) == (None, "12 pancakes")
