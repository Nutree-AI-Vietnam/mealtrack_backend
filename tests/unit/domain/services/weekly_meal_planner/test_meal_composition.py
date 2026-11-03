"""Composing a meal from catalog dishes, portions and rice."""

from decimal import Decimal

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    LOGGABLE_PORTIONS,
    MAX_RICE_STEPS,
    MEAL_KCAL_SHARE_CAP,
    DayTargets,
    compose_day,
    day_totals,
)


def _meal(meal_id: str, *, protein="30", carbs="40", fat="10", fiber="5"):
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name=meal_id,
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal(protein),
        carbs_g=Decimal(carbs),
        fat_g=Decimal(fat),
        fiber_g=Decimal(fiber),
        meal_types=("breakfast", "lunch", "dinner"),
        popularity_rank=1,
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=1,
                display_name=meal_id,
                quantity=Decimal("100"),
                unit="g",
            ),
        ),
    )


def _targets(kcal=2000.0):
    """Targets that add up: 25 % protein, 50 % carbs, 25 % fat."""
    return DayTargets(
        kcal=kcal,
        protein=kcal * 0.25 / 4,
        carbs=kcal * 0.50 / 4,
        fat=kcal * 0.25 / 9,
    )


def _pool(n=8):
    return tuple(
        _meal(
            f"m{i}",
            protein=str(18 + 4 * i),
            carbs=str(25 + 9 * i),
            fat=str(7 + 3 * i),
        )
        for i in range(n)
    )


def _miss(composed, meals_by_id, targets) -> float:
    actual = day_totals(composed, meals_by_id)
    wanted = (targets.kcal, targets.protein, targets.carbs, targets.fat)
    return sum(abs(a - t) / t for a, t in zip(actual, wanted, strict=True)) / len(
        wanted
    )


def test_composing_fits_the_day_better_than_serving_whole_dishes():
    pool = _pool()
    meals_by_id = {meal.id: meal for meal in pool}
    targets = _targets()

    composed = compose_day((pool, pool, pool), targets)

    # The same dishes served at one portion each, which is what the plan does
    # today, for comparison.
    plain = [
        slot.__class__(slot.day_index, slot.slot_index, slot.recipe_id)
        for slot in composed
    ]

    assert _miss(composed, meals_by_id, targets) < _miss(plain, meals_by_id, targets)
    # Calories land on the day; the rest of the miss is what an eight-dish
    # pool of fixed macro ratios can reach at all.
    assert (
        abs(day_totals(composed, meals_by_id)[0] - targets.kcal) < 0.05 * targets.kcal
    )
    assert _miss(composed, meals_by_id, targets) < 0.12


def test_every_portion_is_one_the_app_can_log():
    composed = compose_day((_pool(), _pool(), _pool()), _targets())

    assert all(slot.portion in LOGGABLE_PORTIONS for slot in composed)


def test_a_rice_dish_is_never_planned_without_rice():
    pool = _pool()
    rice_dishes = frozenset({"m0", "m1", "m2", "m3", "m4", "m5"})

    composed = compose_day((pool, pool, pool), _targets(), rice_dishes=rice_dishes)

    lunch_and_dinner = [slot for slot in composed if slot.slot_index > 0]
    assert all(1 <= slot.rice_steps <= MAX_RICE_STEPS for slot in lunch_and_dinner)
    assert all(slot.rice_grams == slot.rice_steps * 55 for slot in composed)


def test_rice_only_goes_with_dishes_that_take_it():
    pool = _pool()

    composed = compose_day((pool, pool, pool), _targets(), rice_dishes=frozenset())

    assert all(slot.rice_steps == 0 for slot in composed)


def test_no_meal_takes_more_than_its_share_of_the_day():
    pool = _pool()
    meals_by_id = {meal.id: meal for meal in pool}
    targets = _targets(1400.0)

    composed = compose_day((pool, pool, pool), targets)

    for slot in composed:
        kcal = day_totals([slot], meals_by_id)[0]
        assert kcal <= MEAL_KCAL_SHARE_CAP * targets.kcal + 1e-6


def test_a_bigger_day_is_served_more_food():
    pool = _pool()
    meals_by_id = {meal.id: meal for meal in pool}

    small = compose_day((pool, pool, pool), _targets(1400.0))
    large = compose_day((pool, pool, pool), _targets(2800.0))

    assert day_totals(large, meals_by_id)[0] > day_totals(small, meals_by_id)[0] * 1.5


def test_composing_is_stable_for_the_same_inputs():
    pool = _pool()

    assert compose_day((pool, pool, pool), _targets()) == compose_day(
        (pool, pool, pool), _targets()
    )


def test_a_slot_with_no_candidate_stays_empty():
    pool = _pool()

    composed = compose_day((pool, (), pool), _targets())

    assert composed[1].recipe_id is None
    assert composed[0].recipe_id is not None
