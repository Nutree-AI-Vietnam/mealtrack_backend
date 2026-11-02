"""Sanitising AI output, quality gates and ingredient weights."""

import pytest

from src.domain.exceptions.recipe_import_exceptions import (
    RecipeImportError,
    RecipeImportFailure,
)
from src.domain.model.common.enums import MealType
from src.domain.model.recipe_import import (
    ExtractedIngredient,
    ExtractedRecipe,
    RecipeSourceMaterial,
    RecipeSourcePlatform,
    RecipeStep,
)
from src.domain.model.recipe_import.ingredient_weight import (
    canonical_unit,
    ingredient_grams,
    is_measured_unit,
)
from src.domain.model.recipe_import.quality import (
    caption_is_enough,
    has_substantial_caption,
    require_recipe,
)
from src.domain.model.recipe_import.sanitizer import sanitize_extracted_recipe


def _recipe(*ingredients: ExtractedIngredient, steps: int = 1) -> ExtractedRecipe:
    return ExtractedRecipe(
        is_recipe=True,
        name="Soup",
        language="en",
        ingredients=ingredients,
        steps=tuple(RecipeStep(f"step {i}") for i in range(steps)),
    )


def _item(name: str, quantity: float | None = 1, optional: bool = False):
    return ExtractedIngredient(
        name=name, english_name=name, quantity=quantity, optional=optional
    )


class TestSanitizer:
    def test_trims_and_bounds_untrusted_fields(self):
        recipe = sanitize_extracted_recipe(
            {
                "is_recipe": True,
                "name": "  <b>Gà</b> chiên  ",
                "servings": 250,
                "prep_time_minutes": -5,
                "cook_time_minutes": 30.4,
                "meal_type": "DINNER",
                "ingredients": [
                    {
                        "name": "cánh gà",
                        "english_name": "chicken wing, raw",
                        "quantity": 500,
                        "unit": "g",
                        "grams": 500,
                    },
                    {"name": "", "english_name": "x"},
                    {
                        "name": "muối",
                        "english_name": None,
                        "quantity": float("inf"),
                        "grams": -1,
                    },
                    "not an object",
                ],
                "steps": [
                    {"instruction": "Chiên", "duration_minutes": 9999},
                    {"instruction": " "},
                    "Rắc muối",
                ],
            },
            language="vi",
        )

        assert recipe.name == "Gà chiên"
        assert recipe.servings is None
        assert (recipe.prep_time_minutes, recipe.cook_time_minutes) == (None, 30)
        assert recipe.meal_type == MealType.DINNER
        assert [i.name for i in recipe.ingredients] == ["cánh gà", "muối"]
        salt = recipe.ingredients[1]
        assert (salt.english_name, salt.quantity, salt.grams) == ("muối", None, None)
        assert recipe.steps == (RecipeStep("Chiên", None), RecipeStep("Rắc muối", None))

    def test_a_recipe_without_a_name_is_not_a_recipe(self):
        recipe = sanitize_extracted_recipe(
            {"is_recipe": True, "name": ""}, language="en"
        )

        assert not recipe.is_recipe

    def test_lists_are_capped(self):
        recipe = sanitize_extracted_recipe(
            {
                "is_recipe": True,
                "name": "Big",
                "ingredients": [
                    {"name": f"i{n}", "english_name": "x"} for n in range(200)
                ],
                "steps": [f"s{n}" for n in range(200)],
            },
            language="en",
        )

        assert (len(recipe.ingredients), len(recipe.steps)) == (80, 60)


class TestQuality:
    def test_caption_with_quantified_ingredients_and_steps_is_enough(self):
        assert caption_is_enough(_recipe(_item("a"), _item("b"), _item("c")))

    def test_caption_without_amounts_is_not_enough(self):
        recipe = _recipe(_item("a", None), _item("b", None), _item("c"))

        assert not caption_is_enough(recipe)

    def test_caption_without_steps_is_not_enough(self):
        assert not caption_is_enough(
            _recipe(_item("a"), _item("b"), _item("c"), steps=0)
        )

    def test_optional_garnish_does_not_count(self):
        recipe = _recipe(_item("a"), _item("b"), _item("parsley", None, optional=True))

        assert not caption_is_enough(recipe)
        assert recipe.quantified_share == 1.0

    def test_require_recipe_raises_not_a_recipe(self):
        empty = ExtractedRecipe(
            is_recipe=False, name="", language="en", rejection_reason="a dance video"
        )

        with pytest.raises(RecipeImportError) as caught:
            require_recipe(empty)

        assert caught.value.failure == RecipeImportFailure.NOT_A_RECIPE
        assert caught.value.details == {"reason": "a dance video"}

    def test_short_captions_are_not_substantial(self):
        short = RecipeSourceMaterial(
            platform=RecipeSourcePlatform.TIKTOK, description="Yum 😋 #food"
        )
        long = RecipeSourceMaterial(
            platform=RecipeSourcePlatform.TIKTOK, description="x" * 90
        )

        assert not has_substantial_caption(short)
        assert has_substantial_caption(long)


class TestIngredientWeight:
    @pytest.mark.parametrize(
        ("name", "quantity", "unit", "estimate", "grams"),
        [
            ("beef", 1, "kg", 900, 1000.0),
            ("beef", 8, "oz", None, 226.8),
            ("olive oil", 2, "tbsp", None, 27.6),
            ("fish sauce", 2, "muỗng canh", None, 36.0),
            ("whole milk", 1, "cup", 200, 247.2),
            ("flour", 1.5, "cups", 190, 190),
            ("flour", 1.5, "cups", None, 360.0),
            ("garlic", 3, "clove", 9, 9),
            ("salt", None, None, None, None),
            ("rice", 100, "kg", None, None),
        ],
    )
    def test_weighs_lines(self, name, quantity, unit, estimate, grams):
        assert (
            ingredient_grams(
                english_name=name,
                quantity=quantity,
                unit=unit,
                estimated_grams=estimate,
            )
            == grams
        )

    def test_units_are_canonicalised(self):
        assert canonical_unit("Tablespoons") == "tablespoon"
        assert canonical_unit("Grams") == "g"
        assert canonical_unit("") is None
        assert is_measured_unit("ml") and not is_measured_unit("piece")


class TestEstimatedAmounts:
    def test_sanitizer_keeps_estimates_and_bounds_eaten_share(self):
        recipe = sanitize_extracted_recipe(
            {
                "is_recipe": True,
                "name": "Cơm tấm",
                "ingredients": [
                    {
                        "name": "sườn",
                        "english_name": "pork chop",
                        "quantity": 400,
                        "unit": "g",
                        "grams": 400,
                        "amount_estimated": True,
                        "eaten_share": None,
                    },
                    {
                        "name": "nước mắm pha",
                        "english_name": "fish sauce",
                        "grams": 300,
                        "eaten_share": 0.25,
                    },
                    {
                        "name": "dầu chiên",
                        "english_name": "vegetable oil",
                        "grams": 500,
                        "eaten_share": 0,
                    },
                    {
                        "name": "nước ngâm",
                        "english_name": "water",
                        "grams": 500,
                        "eaten_share": 7,
                    },
                ],
            },
            language="vi",
        )

        shares = [item.eaten_share for item in recipe.ingredients]
        assert shares == [1.0, 0.25, 0.05, 1.0]
        assert recipe.ingredients[0].amount_estimated is True
        assert recipe.ingredients[1].amount_estimated is False

    def test_estimated_amounts_do_not_count_as_stated(self):
        estimated = ExtractedIngredient(
            name="a", english_name="a", quantity=100, unit="g", amount_estimated=True
        )
        stated = ExtractedIngredient(
            name="b", english_name="b", quantity=2, unit="piece"
        )

        recipe = _recipe(estimated, estimated, stated)

        assert recipe.quantified_share == pytest.approx(1 / 3)
        assert not caption_is_enough(recipe)

    def test_hashtags_and_links_do_not_make_a_caption_substantial(self):
        caption = (
            "Làm dĩa cơm tấm siêu sườn ngon quá ạ #ancungtiktok #reviewanngon "
            "#anngonnaugon #tranquocthai #gdlfamily 😋 @bon.tq1 https://example.com/x"
        )

        material = RecipeSourceMaterial(
            platform=RecipeSourcePlatform.TIKTOK, description=caption
        )

        assert not has_substantial_caption(material)
