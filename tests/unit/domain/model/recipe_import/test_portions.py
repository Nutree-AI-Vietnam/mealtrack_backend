"""The portion pass: checked answer, merged without touching stated amounts."""

from src.domain.model.recipe_import import ExtractedIngredient, ExtractedRecipe
from src.domain.model.recipe_import.portions import (
    PortionEstimate,
    PortionLine,
    apply_portion_estimate,
    sanitize_portion_estimate,
)


def _recipe(
    *items: ExtractedIngredient, servings=None, stated=False
) -> ExtractedRecipe:
    return ExtractedRecipe(
        is_recipe=True,
        name="Cơm tấm",
        language="vi",
        servings=servings,
        servings_stated=stated,
        ingredients=items,
    )


def test_sanitize_drops_bad_lines_and_bounds_values():
    estimate = sanitize_portion_estimate(
        {
            "servings": 4,
            "lines": [
                {
                    "index": 0,
                    "grams": 300,
                    "quantity": 300,
                    "unit": "g",
                    "eaten_share": 1,
                },
                {
                    "index": 1,
                    "grams": -5,
                    "quantity": None,
                    "unit": None,
                    "eaten_share": 0.3,
                },
                {"index": 1, "grams": 99, "eaten_share": 1},
                {"index": 9, "grams": 10, "eaten_share": 1},
                {"index": True, "grams": 10},
                "junk",
            ],
        },
        line_count=2,
    )

    assert estimate.servings == 4
    assert estimate.lines == (
        PortionLine(0, 300.0, 300.0, "g", 1.0),
        PortionLine(1, None, None, None, 0.3),
    )
    assert sanitize_portion_estimate({"servings": 0}, line_count=1).servings is None


def test_estimates_fill_missing_amounts_and_shares_but_keep_stated_ones():
    recipe = _recipe(
        ExtractedIngredient("trứng vịt", "duck egg", quantity=2, unit="piece"),
        ExtractedIngredient("sườn", "pork chop"),
        ExtractedIngredient("đường", "sugar", quantity=1, unit="cup", grams=200),
        ExtractedIngredient("muối", "salt"),
    )
    estimate = PortionEstimate(
        servings=4,
        lines=(
            PortionLine(0, 140.0, None, None, 1.0),
            PortionLine(1, 600.0, 4.0, "piece", 1.0),
            PortionLine(2, 999.0, 5.0, "kg", 0.25),
            PortionLine(3, None, None, None, 1.0),
        ),
    )

    sized = apply_portion_estimate(recipe, estimate)

    egg, chop, sugar, salt = sized.ingredients
    assert (egg.quantity, egg.unit, egg.grams, egg.amount_estimated) == (
        2,
        "piece",
        140.0,
        False,
    )
    assert (chop.quantity, chop.unit, chop.grams, chop.amount_estimated) == (
        4.0,
        "piece",
        600.0,
        True,
    )
    # Stated amounts stay; only the eaten share changes.
    assert (sugar.quantity, sugar.unit, sugar.grams, sugar.eaten_share) == (
        1,
        "cup",
        200,
        0.25,
    )
    assert (salt.grams, salt.amount_estimated) == (None, False)
    assert sized.servings == 4


def test_estimated_weight_without_a_count_shows_in_grams():
    sized = apply_portion_estimate(
        _recipe(ExtractedIngredient("nấm mèo", "wood ear mushroom")),
        PortionEstimate(servings=None, lines=(PortionLine(0, 10.0, None, None, 1.0),)),
    )

    assert (sized.ingredients[0].quantity, sized.ingredients[0].unit) == (10.0, "g")


def test_stated_servings_are_kept():
    sized = apply_portion_estimate(
        _recipe(ExtractedIngredient("a", "a"), servings=2, stated=True),
        PortionEstimate(servings=6, lines=()),
    )

    assert sized.servings == 2


def test_a_count_without_a_unit_shows_the_weight():
    sized = apply_portion_estimate(
        _recipe(ExtractedIngredient("trứng vịt", "duck egg")),
        PortionEstimate(servings=None, lines=(PortionLine(0, 140.0, 2.0, None, 1.0),)),
    )

    assert (sized.ingredients[0].quantity, sized.ingredients[0].unit) == (140.0, "g")
