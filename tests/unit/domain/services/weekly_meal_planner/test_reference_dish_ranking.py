"""Shortlisting the catalog dishes worth considering for a slot."""

from decimal import Decimal

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    ScoredDish,
    SlotBrief,
    dish_fresh_ingredient_ids,
    rank_reference_dishes,
)


def _meal(
    meal_id, *, kcal_carbs="40", ingredient_ids=(1,), cuisine="vietnamese", names=None
):
    names = names or [f"ingredient-{i}" for i in ingredient_ids]
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=meal_id,
        cuisine=cuisine,
        description=None,
        image_url=None,
        protein_g=Decimal("30"),
        carbs_g=Decimal(kcal_carbs),
        fat_g=Decimal("10"),
        fiber_g=Decimal("5"),
        meal_types=("breakfast", "lunch", "dinner"),
        popularity_rank=1,
        ingredients=tuple(
            CatalogMealIngredient(
                food_reference_id=food_id,
                display_name=name,
                quantity=Decimal("100"),
                unit="g",
            )
            for food_id, name in zip(ingredient_ids, names, strict=True)
        ),
    )


def _brief(**overrides):
    base = {"slot_index": 2, "kcal_target": 750.0}
    base.update(overrides)
    return SlotBrief(**base)


def test_seasonings_do_not_count_as_shopping_for_a_dish():
    meal = _meal("m", ingredient_ids=(1, 2, 3), names=["thịt heo", "nước mắm", "tỏi"])

    assert dish_fresh_ingredient_ids(meal) == frozenset({1})


def test_a_dish_that_shops_from_the_basket_outranks_one_that_does_not():
    shared = _meal("shared", ingredient_ids=(1, 2), names=["thịt heo", "cà rốt"])
    other = _meal("other", ingredient_ids=(8, 9), names=["cá hồi", "măng tây"])

    ranked = rank_reference_dishes([other, shared], _brief(basket=frozenset({1, 2})))

    assert ranked[0].meal.id == "shared"
    assert ranked[0].basket == 1.0
    assert "mostly uses what the week already buys" in ranked[0].reasons


def test_food_the_user_eats_often_lifts_a_dish():
    liked = _meal("liked", ingredient_ids=(1, 2), names=["thịt heo", "cà rốt"])
    plain = _meal("plain", ingredient_ids=(8, 9), names=["cá hồi", "măng tây"])

    ranked = rank_reference_dishes(
        [plain, liked], _brief(liked_ingredients=frozenset({1, 2}))
    )

    assert ranked[0].meal.id == "liked"


def test_a_dish_that_cannot_reach_the_slot_scores_low_on_fit():
    tiny = _meal("tiny", kcal_carbs="1")
    right = _meal("right", kcal_carbs="110")

    ranked = rank_reference_dishes([tiny, right], _brief(kcal_target=750.0))

    assert ranked[0].meal.id == "right"
    assert ranked[0].fit > ranked[-1].fit


def test_excluded_and_off_cuisine_dishes_are_left_out():
    ranked = rank_reference_dishes(
        [_meal("skip"), _meal("japanese", cuisine="japanese"), _meal("keep")],
        _brief(exclude_recipe_ids=frozenset({"skip"}), cuisine="vietnamese"),
    )

    assert [dish.meal.id for dish in ranked] == ["keep"]


def test_semantic_scores_change_the_order_when_they_are_available():
    a, b = _meal("a"), _meal("b")
    brief = _brief()

    without = rank_reference_dishes([a, b], brief)
    with_vectors = rank_reference_dishes(
        [a, b], brief, embedding_scores={"b": 1.0, "a": 0.0}
    )

    assert [dish.meal.id for dish in without] == ["a", "b"]
    assert with_vectors[0].meal.id == "b"
    assert with_vectors[0].semantic == 1.0


def test_scores_stay_on_one_scale_with_or_without_vectors():
    meals = [_meal("a", ingredient_ids=(1,), names=["thịt heo"])]
    # 360 kcal a serving, so two servings land exactly on the slot: every leg
    # scores 1, and the weights must still add up to 1 either way.
    brief = _brief(
        kcal_target=720.0, basket=frozenset({1}), liked_ingredients=frozenset({1})
    )

    without = rank_reference_dishes(meals, brief)[0]
    with_vectors = rank_reference_dishes(meals, brief, embedding_scores={"a": 1.0})[0]

    assert without.fit == 1.0
    # A dish that is perfect on every leg scores 1 whether or not the vector
    # leg ran, so the two modes are on one scale.
    assert abs(without.score - 1.0) < 1e-9
    assert abs(with_vectors.score - 1.0) < 1e-9


def test_the_shortlist_is_capped_and_stable():
    meals = [_meal(f"m{i:02d}") for i in range(30)]

    first = rank_reference_dishes(meals, _brief(), limit=5)
    again = rank_reference_dishes(meals, _brief(), limit=5)

    assert len(first) == 5
    assert [dish.meal.id for dish in first] == [dish.meal.id for dish in again]
    assert all(isinstance(dish, ScoredDish) for dish in first)
