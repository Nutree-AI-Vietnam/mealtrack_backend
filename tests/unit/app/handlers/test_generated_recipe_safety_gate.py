"""A generated recipe passes the food-safety rules before anyone sees it."""

import pytest

from src.api.exceptions import ExternalServiceException
from src.app.handlers.command_handlers.meal_suggestion.generate_meal_recipes_command_handler import (  # noqa: E501
    _draft_from,
    _drop_unsafe_recipes,
)
from src.domain.model.meal_suggestion import (
    Ingredient,
    MealSuggestion,
    MealType,
    RecipeStep,
)
from src.domain.model.meal_suggestion.meal_suggestion import MacroEstimate


def _recipe(name, ingredients, steps):
    return MealSuggestion(
        id="s1",
        session_id="sess",
        user_id="user-1",
        meal_name=name,
        description="",
        meal_type=MealType.DINNER,
        macros=MacroEstimate(calories=500, protein=30, carbs=40, fat=20),
        ingredients=[Ingredient(name=n, amount=a, unit=u) for n, a, u in ingredients],
        recipe_steps=[
            RecipeStep(step=i + 1, instruction=s) for i, s in enumerate(steps)
        ],
        prep_time_minutes=30,
        confidence_score=0.9,
    )


_GOOD = _recipe(
    "Thịt kho trứng",
    [("thịt ba chỉ", 150, "g"), ("trứng gà", 100, "g"), ("dầu ăn", 10, "g")],
    ["Kho 45 phút đến khi thịt chín mềm."],
)


def test_a_properly_cooked_recipe_survives_without_any_resolver():
    """Nothing in this path matches ingredients, and that must not matter.

    The nutrition half of the review rejects any unidentified line over
    10 g. Applying it here would discard every recipe, this one included.
    """
    assert _drop_unsafe_recipes([_GOOD]) == [_GOOD]


def test_a_blocked_dish_never_reaches_the_user():
    unsafe = _recipe(
        "Tiết canh vịt", [("tiết vịt", 200, "g")], ["Đánh tiết, để đông, ăn ngay."]
    )

    assert _drop_unsafe_recipes([unsafe, _GOOD]) == [_GOOD]


def test_raw_meat_with_no_cooking_step_is_dropped():
    raw = _recipe(
        "Gỏi gà bắp cải",
        [("thịt gà", 250, "g"), ("bắp cải", 150, "g")],
        ["Xé thịt gà.", "Trộn với bắp cải, dọn ra."],
    )

    assert _drop_unsafe_recipes([raw]) == []


def test_a_fried_dish_must_account_for_its_fat():
    fried = _recipe(
        "Rau muống xào tỏi",
        [("rau muống", 300, "g"), ("tỏi", 10, "g")],
        ["Xào 5 phút cho chín."],
    )

    assert _drop_unsafe_recipes([fried]) == []


def test_a_counted_ingredient_has_no_weight_rather_than_a_guessed_one():
    """Three eggs is three eggs; nobody here decides what one weighs."""
    counted = _recipe(
        "Trứng chiên", [("trứng gà", 3, "quả"), ("dầu ăn", 10, "g")], ["Chiên chín."]
    )

    draft = _draft_from(counted)

    assert draft.ingredients[0].grams == 0.0
    assert draft.ingredients[1].grams == 10.0


def test_the_steps_come_through_for_the_doneness_rule():
    draft = _draft_from(_GOOD)

    assert draft.name == "Thịt kho trứng"
    assert draft.steps == ("Kho 45 phút đến khi thịt chín mềm.",)


@pytest.mark.parametrize("empty", [[], None])
def test_a_recipe_with_no_ingredients_is_not_a_recipe(empty):
    hollow = _recipe("Món rỗng", [], ["Nấu chín."])
    hollow.ingredients = empty

    assert _drop_unsafe_recipes([hollow]) == []


def test_the_exception_type_exists_for_an_all_unsafe_batch():
    """The handler raises this when nothing survives; keep the name stable."""
    assert issubclass(ExternalServiceException, Exception)
