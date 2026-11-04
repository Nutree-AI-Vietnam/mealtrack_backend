"""A planned day reports what it still leaves on the table."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

from src.api.routes.v1.meal_plans import SNACK_WORTH_EATING_KCAL, _day_summaries
from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import (
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)


def _meal(meal_id="m1", protein="30", carbs="40", fat="10", fiber="5"):
    return CatalogMeal(
        id=meal_id,
        catalog_key=meal_id,
        content_hash="a" * 64,
        name="Thịt kho",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=Decimal(protein),
        carbs_g=Decimal(carbs),
        fat_g=Decimal(fat),
        fiber_g=Decimal(fiber),
        meal_types=("lunch",),
    )


def _plan(daily_calories, *, recipe_id="m1", portion=1.0, rice_steps=0):
    return WeeklyMealPlan(
        id="plan-1",
        user_id="user-1",
        week_start_date=date(2026, 9, 21),
        status=WeeklyMealPlanStatus.DRAFT,
        people=1,
        preferences=WeeklyMealPlanPreferences(people=1),
        timezone="UTC",
        daily_calories=daily_calories,
        slots=tuple(
            WeeklyMealPlanSlot(
                id=f"slot-{day}-{slot}",
                day_index=day,
                slot_index=slot,
                recipe_id=recipe_id,
                planned_portion=portion,
                planned_rice_steps=rice_steps,
            )
            for day in range(7)
            for slot in range(WEEKLY_SLOTS_PER_DAY)
        ),
        created_at=datetime(2026, 9, 20),
        updated_at=datetime(2026, 9, 20),
    )


def test_every_day_reports_what_it_adds_up_to():
    meal = _meal()
    summaries = _day_summaries(_plan(2000), {"m1": meal})

    assert len(summaries) == 7
    assert [day.day_index for day in summaries] == list(range(7))
    # Three slots of one serving each.
    assert summaries[0].planned_calories == round(meal.calories * 3)
    assert summaries[0].target_calories == 2000


def test_a_day_short_of_its_target_asks_for_a_snack():
    meal = _meal()
    three_servings = meal.calories * 3
    target = int(three_servings) + 300

    summaries = _day_summaries(_plan(target), {"m1": meal})

    assert summaries[0].snack_calories is not None
    assert 290 <= summaries[0].snack_calories <= 310


def test_a_day_on_target_asks_for_nothing():
    meal = _meal()
    summaries = _day_summaries(_plan(round(meal.calories * 3)), {"m1": meal})

    assert all(day.snack_calories is None for day in summaries)


def test_a_day_over_its_target_asks_for_nothing():
    meal = _meal()
    summaries = _day_summaries(_plan(round(meal.calories * 2)), {"m1": meal})

    assert all(day.snack_calories is None for day in summaries)


def test_a_gap_too_small_to_be_worth_eating_is_not_reported():
    """Nobody needs telling to eat 40 more calories."""
    meal = _meal()
    target = round(meal.calories * 3) + SNACK_WORTH_EATING_KCAL - 10

    summaries = _day_summaries(_plan(target), {"m1": meal})

    assert all(day.snack_calories is None for day in summaries)


def test_the_rice_beside_the_dish_counts_toward_the_day():
    meal = _meal()
    plain = _day_summaries(_plan(2000), {"m1": meal})
    with_rice = _day_summaries(_plan(2000, rice_steps=2), {"m1": meal})

    assert with_rice[0].planned_calories > plain[0].planned_calories


def test_a_bigger_portion_counts_toward_the_day():
    meal = _meal()
    plain = _day_summaries(_plan(2000), {"m1": meal})
    double = _day_summaries(_plan(2000, portion=2.0), {"m1": meal})

    assert double[0].planned_calories == plain[0].planned_calories * 2


def test_a_plan_with_no_target_reports_calories_but_no_snack():
    """Without a target there is nothing to be short of."""
    summaries = _day_summaries(_plan(None), {"m1": _meal()})

    assert summaries[0].planned_calories > 0
    assert summaries[0].target_calories is None
    assert summaries[0].snack_calories is None


def test_an_empty_day_asks_for_no_snack():
    """An unplanned day is not a day the user is under-eating."""
    plan = _plan(2000)
    empty = replace(
        plan, slots=tuple(replace(slot, recipe_id=None) for slot in plan.slots)
    )

    summaries = _day_summaries(empty, {})

    assert summaries[0].planned_calories == 0
    assert summaries[0].snack_calories is None
