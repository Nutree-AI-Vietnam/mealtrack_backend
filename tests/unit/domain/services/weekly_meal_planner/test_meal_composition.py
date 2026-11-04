"""Composing a meal from catalog dishes, portions and rice."""

from dataclasses import replace
from decimal import Decimal

import pytest

from src.domain.model.meal_recommendation import CatalogMeal, CatalogMealIngredient
from src.domain.model.weekly_meal_planner.meal_locale import MealLocale
from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
    LOGGABLE_PORTIONS,
    MAX_RICE_STEPS,
    MEAL_KCAL_SHARE_CAP,
    ComposedSlot,
    DayTargets,
    _macro_cost,
    compose_day,
    day_totals,
    meal_macros,
    meal_serving_calories,
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

    with_rice = [
        slot
        for slot in composed
        if slot.slot_index > 0 and slot.recipe_id in rice_dishes
    ]
    assert with_rice
    assert all(1 <= slot.rice_steps <= MAX_RICE_STEPS for slot in with_rice)
    assert all(slot.rice_grams == slot.rice_steps * 55 for slot in composed)


def test_breakfast_never_comes_with_a_bowl_of_rice():
    """Breakfast is pho, banh mi, xoi or chao: no plain rice beside it.

    The dish catalogue decides which dishes are eaten with rice by how they
    are cooked, so a fried-egg breakfast matches. The slot has to say no.
    """
    pool = _pool()
    every_dish_takes_rice = frozenset(meal.id for meal in pool)

    composed = compose_day(
        (pool, pool, pool), _targets(), rice_dishes=every_dish_takes_rice
    )

    breakfast = next(slot for slot in composed if slot.slot_index == 0)
    assert breakfast.rice_steps == 0
    assert breakfast.rice_grams == 0
    assert all(slot.rice_steps >= 1 for slot in composed if slot.slot_index > 0)


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


def test_the_same_dish_is_never_served_twice_in_a_day():
    pool = _pool()

    composed = compose_day((pool, pool, pool), _targets())

    served = [slot.recipe_id for slot in composed if slot.recipe_id]
    assert len(set(served)) == len(served)


def test_a_dish_already_served_this_week_costs_more():
    pool = _pool()
    targets = _targets()

    fresh = compose_day((pool, pool, pool), targets)
    best = fresh[2].recipe_id
    # Serve that dish four times earlier in the week and it should give way.
    later = compose_day((pool, pool, pool), targets, used_counts={best: 4})

    assert later[2].recipe_id != best


def test_a_week_of_days_does_not_repeat_one_menu():
    pool = _pool()
    served: dict[str, int] = {}
    week = []
    for day in range(7):
        composed = compose_day(
            (pool, pool, pool), _targets(), day_index=day, used_counts=served
        )
        week.append(composed)
        for slot in composed:
            if slot.recipe_id:
                served[slot.recipe_id] = served.get(slot.recipe_id, 0) + 1

    assert len(served) >= 5
    # The same three dishes every day would mean one distinct menu.
    menus = {tuple(slot.recipe_id for slot in day) for day in week}
    assert len(menus) > 1


def _bread_locale():
    """A region that eats its staple at breakfast too, in 40 g steps."""
    return MealLocale(
        code="test",
        staple_name="Bread",
        staple_step_grams=40,
        staple_step_macros=(3.0, 20.0, 1.0),
        max_staple_steps=3,
        min_staple_steps=1,
        staple_slots=frozenset({0, 1, 2}),
        slot_calorie_shares=(0.3, 0.3, 0.4),
        staple_dish_words=("stew",),
        standalone_dish_words=("soup",),
        staple_item_words=("bread",),
    )


def test_another_locale_may_serve_its_staple_at_breakfast():
    """The breakfast rule belongs to Viet Nam, not to the composer."""
    pool = _pool()
    every_dish = frozenset(meal.id for meal in pool)

    composed = compose_day(
        (pool, pool, pool),
        _targets(),
        rice_dishes=every_dish,
        locale=_bread_locale(),
    )

    breakfast = next(slot for slot in composed if slot.slot_index == 0)
    assert breakfast.rice_steps >= 1


def test_the_staple_grams_follow_the_locale_that_composed_the_slot():
    """A 40 g step must not be reported in Viet Nam's 55 g bowls."""
    pool = _pool()
    every_dish = frozenset(meal.id for meal in pool)

    composed = compose_day(
        (pool, pool, pool),
        _targets(),
        rice_dishes=every_dish,
        locale=_bread_locale(),
    )

    for slot in composed:
        assert slot.rice_grams == slot.rice_steps * 40


def _day_kcal(composed, meals_by_id):
    return day_totals(composed, meals_by_id)[0]


def test_a_day_lands_on_target_or_a_little_over_not_under():
    """Under-eating is the worse miss: the user gets hungry and eats off plan."""
    pool = _pool()
    meals_by_id = {meal.id: meal for meal in pool}
    targets = _targets()

    under_days = 0
    for day in range(7):
        composed = compose_day((pool, pool, pool), targets, day_index=day)
        if _day_kcal(composed, meals_by_id) < targets.kcal:
            under_days += 1

    assert under_days <= 2


def test_a_shortfall_costs_more_than_the_same_surplus():
    targets = _targets()
    short = (targets.kcal - 100, targets.protein, targets.carbs, targets.fat)
    over = (targets.kcal + 100, targets.protein, targets.carbs, targets.fat)

    assert _macro_cost(short, targets) > _macro_cost(over, targets)


def test_a_small_surplus_is_free_but_a_large_one_is_not():
    """The free band is narrow: a cut that is always overshot is not a cut."""
    targets = _targets()
    on_target = (targets.kcal, targets.protein, targets.carbs, targets.fat)
    just_over = (targets.kcal * 1.02, targets.protein, targets.carbs, targets.fat)
    well_over = (targets.kcal * 1.20, targets.protein, targets.carbs, targets.fat)

    assert _macro_cost(just_over, targets) == pytest.approx(
        _macro_cost(on_target, targets)
    )
    assert _macro_cost(well_over, targets) > _macro_cost(on_target, targets)


def test_the_other_macros_stay_symmetric_apart_from_protein():
    """Only calories and protein are asymmetric; carbs and fat are not."""
    targets = _targets()
    low_fat = (targets.kcal, targets.protein, targets.carbs, targets.fat - 10)
    high_fat = (targets.kcal, targets.protein, targets.carbs, targets.fat + 10)

    assert _macro_cost(low_fat, targets) == pytest.approx(
        _macro_cost(high_fat, targets)
    )


def _pot(meal_id="pot", grams=1200, protein="240", carbs="480", fat="80"):
    """A recipe written for the pot, as every crawled recipe is."""
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name="Hủ tiếu",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal(protein),
        carbs_g=Decimal(carbs),
        fat_g=Decimal(fat),
        fiber_g=Decimal("20"),
        meal_types=("lunch",),
        ingredients=(
            CatalogMealIngredient(
                food_reference_id=1,
                display_name="nguyên liệu",
                quantity=Decimal(str(grams)),
                unit="g",
            ),
        ),
    )


def test_a_pot_of_food_is_not_one_serving():
    """Staging has 920 recipes and not one states its servings."""
    pot = _pot()

    protein, carbs, fat, _ = meal_macros(pot)

    # 1200 g of solids reads as about five servings.
    assert protein == pytest.approx(240 / 5)
    assert carbs == pytest.approx(480 / 5)
    assert fat == pytest.approx(80 / 5)


def test_a_recipe_that_states_its_servings_is_believed():
    stated = replace(_pot(), base_servings=3, serving_confidence="verified")

    protein, _, _, _ = meal_macros(stated)

    assert protein == pytest.approx(240 / 3)


def test_a_stated_serving_count_nobody_checked_is_not_believed():
    """serving_confidence unknown means the number came from nowhere."""
    claimed = replace(_pot(), base_servings=1, serving_confidence="unknown")

    protein, _, _, _ = meal_macros(claimed)

    assert protein < 240


def test_a_plate_sized_recipe_stays_one_serving():
    plate = _pot(grams=280, protein="30", carbs="40", fat="10")

    protein, _, _, _ = meal_macros(plate)

    assert protein == pytest.approx(30)


def test_a_recipe_too_light_to_judge_is_left_alone():
    """Under the weighed floor there is nothing honest to divide by."""
    tiny = _pot(grams=40, protein="5", carbs="5", fat="1")

    protein, _, _, _ = meal_macros(tiny)

    assert protein == pytest.approx(5)


def test_serving_calories_follow_the_divided_macros():
    pot = _pot()

    assert meal_serving_calories(pot) == pytest.approx(
        day_totals((ComposedSlot(0, 1, pot.id),), {pot.id: pot})[0]
    )
