"""Rules a generated recipe must pass before anyone sees it."""

import pytest

from src.domain.services.weekly_meal_planner.recipe_publication import (
    INGREDIENT_AI_ESTIMATED,
    INGREDIENT_FROM_DATABASE,
    INGREDIENT_UNRESOLVED,
    DraftIngredient,
    RecipeDraft,
    review_recipe_draft,
)


def _line(name, grams=150.0, **overrides):
    base = {
        "name": name,
        "grams": grams,
        "source": INGREDIENT_FROM_DATABASE,
        "food_reference_id": 1,
        "kcal": 200.0,
    }
    base.update(overrides)
    return DraftIngredient(**base)


def _draft(**overrides):
    base = {
        "name": "Thịt heo kho trứng",
        "ingredients": (_line("thịt heo"), _line("trứng gà", grams=100.0)),
        "steps": ("Ướp thịt 15 phút.", "Kho lửa nhỏ cho thịt chín mềm."),
        "servings": 2,
    }
    base.update(overrides)
    return RecipeDraft(**base)


def test_a_sound_recipe_passes_and_may_be_served_to_anyone():
    review = review_recipe_draft(_draft())

    assert review.usable
    assert review.constraints_checkable
    assert review.servable_to(has_dietary_constraint=True)
    assert review.rejections == ()


@pytest.mark.parametrize(
    "dish",
    ["Tiết canh vịt", "Nem chua rán kiểu Bắc", "Gỏi cá trích", "Wild mushroom soup"],
)
def test_dishes_that_are_unsafe_to_generate_are_rejected(dish):
    review = review_recipe_draft(_draft(name=dish))

    assert not review.usable
    assert "dish is unsafe to generate" in review.rejections


def test_a_recipe_that_serves_meat_rare_is_rejected():
    review = review_recipe_draft(
        _draft(name="Bò tái chanh", steps=("Thái bò mỏng, trộn chanh.",))
    )

    assert not review.usable


def test_fresh_bamboo_must_be_boiled():
    rejected = review_recipe_draft(
        _draft(
            ingredients=(_line("măng tươi"), _line("thịt heo")),
            steps=("Xào măng với thịt cho chín.",),
        )
    )
    accepted = review_recipe_draft(
        _draft(
            ingredients=(
                _line("măng tươi"),
                _line("thịt heo"),
                _line("dầu ăn", grams=10.0),
            ),
            steps=("Luộc măng 10 phút, đổ nước.", "Xào với thịt cho chín."),
        )
    )

    assert "măng tươi must be boiled thoroughly" in rejected.rejections
    assert accepted.usable


def test_raw_meat_needs_a_step_that_says_it_is_cooked():
    review = review_recipe_draft(
        _draft(steps=("Cho thịt heo và trứng vào tô.", "Rắc hành lá lên trên."))
    )

    assert "no step says the food is cooked through" in review.rejections


def test_a_fried_dish_must_list_its_cooking_fat():
    review = review_recipe_draft(
        _draft(
            name="Đậu hũ chiên giòn",
            ingredients=(_line("đậu hũ", grams=300.0),),
            steps=("Chiên đậu hũ cho vàng giòn.",),
        )
    )

    assert "a fried dish must list its cooking fat" in review.rejections


def test_a_large_unidentified_ingredient_is_rejected():
    review = review_recipe_draft(
        _draft(
            ingredients=(
                _line("thịt heo"),
                _line(
                    "sốt bí truyền",
                    grams=60.0,
                    source=INGREDIENT_UNRESOLVED,
                    food_reference_id=None,
                ),
            )
        )
    )

    assert "sốt bí truyền could not be identified" in review.rejections


def test_a_small_unidentified_ingredient_blocks_only_constrained_users():
    review = review_recipe_draft(
        _draft(
            ingredients=(
                _line("thịt heo"),
                _line(
                    "nước mắm",
                    grams=8.0,
                    source=INGREDIENT_UNRESOLVED,
                    food_reference_id=None,
                    kcal=5.0,
                ),
            ),
            servings=1,
        )
    )

    # Nutrition survives a splash of something unknown; an allergy does not.
    assert review.usable
    assert not review.constraints_checkable
    assert review.servable_to(has_dietary_constraint=False)
    assert not review.servable_to(has_dietary_constraint=True)


def test_too_much_estimated_energy_is_flagged_for_review():
    review = review_recipe_draft(
        _draft(
            ingredients=(
                _line("thịt heo", kcal=200.0),
                _line("sốt lạ", grams=50.0, source=INGREDIENT_AI_ESTIMATED, kcal=90.0),
            )
        )
    )

    assert review.needs_review
    assert "too much of the energy is an AI estimate" in review.flags
    assert not review.constraints_checkable


def test_raw_rice_matched_to_cooked_rice_is_rejected():
    review = review_recipe_draft(
        _draft(
            ingredients=(_line("gạo tẻ", resolved_name="cơm trắng"), _line("thịt heo"))
        )
    )

    assert any("was matched to" in reason for reason in review.rejections)


@pytest.mark.parametrize("grams", [40.0, 4000.0])
def test_a_serving_that_is_not_a_meal_is_rejected(grams):
    review = review_recipe_draft(
        _draft(ingredients=(_line("thịt heo", grams=grams),), servings=1)
    )

    assert any("a serving weighs" in reason for reason in review.rejections)
