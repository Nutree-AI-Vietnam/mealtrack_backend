"""Weekly recap numbers from the week's food meals."""

from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from src.app.services.weekly_recap_meal_stats import (
    FoodMeal,
    common_log_hour,
    dish_stats,
    food_meals,
    late_snacks,
    low_protein_meal,
    lunch_no_veg_days,
    meal_types_by_day,
    meals_by_day,
    missed_meal,
    photo_share,
    pre_workout_carbs,
    weekend_peaks,
)
from src.domain.model.meal import Meal, MealStatus
from src.domain.model.nutrition import Macros, Nutrition, NutritionOverride
from src.domain.model.weekly_recap import DishCount, MissedMeal, PeakMeal

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MONDAY = date(2026, 10, 5)
USER = "11111111-1111-1111-1111-111111111111"
REMINDERS = {"breakfast": 450, "lunch": 720, "dinner": 1170}


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(MONDAY + timedelta(days=day), time(hour, minute), TZ)


def _meal(
    day: int,
    hour: int,
    minute: int = 0,
    *,
    name: str | None = "Phở bò",
    meal_type: str | None = None,
    source: str | None = "scanner",
    status: MealStatus = MealStatus.READY,
    override: NutritionOverride | None = None,
    fiber: float = 0.0,
) -> Meal:
    created = _local(day, hour, minute).astimezone(UTC)
    nutrition = Nutrition(
        macros=Macros(protein=20.0, carbs=50.0, fat=10.0, fiber=fiber),
        nutrition_override=override,
    )
    return Meal(
        meal_id=str(uuid4()),
        user_id=USER,
        status=status,
        created_at=created,
        image=None,
        dish_name=name,
        nutrition=nutrition if status != MealStatus.PROCESSING else None,
        ready_at=created,
        meal_type=meal_type,
        source=source,
    )


def _food(
    day: int,
    hour: int,
    minute: int = 0,
    *,
    meal_type: str = "lunch",
    name: str = "Phở bò",
    kcal: float = 500.0,
    carbs: float = 50.0,
    fiber: float = 0.0,
    source: str | None = "scanner",
    protein: float = 20.0,
) -> FoodMeal:
    return FoodMeal(
        day=day,
        logged_at=_local(day, hour, minute),
        meal_type=meal_type,
        name=name,
        kcal=kcal,
        carbs=carbs,
        fiber=fiber,
        source=source,
        protein=protein,
    )


def test_food_meals_keeps_the_weeks_analysed_food_in_local_time():
    meals = [
        _meal(2, 19, meal_type="dinner"),
        _meal(0, 8, meal_type="breakfast"),
        _meal(-1, 23),  # previous Sunday
        _meal(7, 0, 30),  # next Monday
        _meal(1, 9, meal_type="hydration", source="hydration"),
        _meal(1, 10, source="hydration"),
        _meal(1, 11, status=MealStatus.PROCESSING),
        _meal(1, 12, status=MealStatus.INACTIVE),
        _meal(1, 13, status=MealStatus.ENRICHING),
    ]

    out = food_meals(meals, MONDAY, TZ)

    assert [(meal.day, meal.logged_at.hour) for meal in out] == [
        (0, 8),
        (1, 13),
        (2, 19),
    ]
    assert out[0].logged_at.tzinfo == TZ
    assert out[0].kcal == 370.0  # 20 g protein, 50 g carbs, 10 g fat
    assert out[0].carbs == 50.0


def test_food_meals_puts_utc_evening_meals_on_the_next_local_day():
    meal = _meal(1, 0, 30)  # Tuesday 00:30 local is Monday 17:30 UTC

    [out] = food_meals([meal], MONDAY, TZ)

    assert meal.created_at.date() == MONDAY
    assert out.day == 1


def test_food_meals_uses_the_users_override_and_tidies_the_name():
    override = NutritionOverride(calories=640.0, protein=30.0, carbs=80.0, fat=20.0)
    meal = _meal(0, 12, name="  Cơm   tấm \n sườn ", override=override, fiber=4.0)

    [out] = food_meals([meal], MONDAY, TZ)

    assert (out.kcal, out.carbs, out.fiber) == (640.0, 80.0, 4.0)
    assert out.name == "Cơm tấm sườn"


@pytest.mark.parametrize(
    ("stored", "hour", "expected"),
    [
        ("dinner", 9, "dinner"),
        (None, 9, "breakfast"),
        (None, 13, "lunch"),
        ("brunch", 19, "dinner"),
        (None, 2, "snack"),
    ],
)
def test_food_meals_keeps_the_stored_meal_type_or_reads_it_from_the_time(
    stored, hour, expected
):
    [out] = food_meals([_meal(3, hour, meal_type=stored)], MONDAY, TZ)

    assert out.meal_type == expected


def test_meals_by_day_counts_at_most_three_a_day():
    meals = [_food(0, hour) for hour in (8, 12, 15, 19)] + [_food(3, 12)]

    assert meals_by_day(meals) == (3, 0, 0, 1, 0, 0, 0)


def test_meal_types_by_day_counts_a_meal_logged_as_two_dishes_once():
    meals = [
        _food(0, 12, name="Cơm"),
        _food(0, 12, 5, name="Thịt kho"),
        _food(0, 19, meal_type="dinner"),
        _food(5, 12, name="Cơm"),
        _food(5, 12, 5, name="Canh chua"),
    ]

    assert meal_types_by_day(meals) == (2, 0, 0, 0, 0, 1, 0)


def test_late_snacks_counts_nights_and_averages_kcal_per_night():
    meals = [
        _food(0, 22, kcal=300),
        _food(0, 23, 30, kcal=100),
        _food(2, 1, kcal=200),  # Wednesday 1 a.m. is Tuesday night
        _food(3, 21, 59, kcal=900),
        _food(4, 4, kcal=900),  # 4 a.m. is morning
        _food(0, 2, kcal=900),  # Sunday night before the week
        _food(5, 12, kcal=900),  # noon: logged for a past day
    ]

    assert late_snacks(meals, 22) == ((0, 1), 300)


def test_late_snacks_without_any():
    assert late_snacks([_food(0, 19)], 22) == ((), 0)


def test_lunch_no_veg_days_adds_up_each_days_lunch_fiber():
    meals = [
        _food(0, 12, fiber=1.0),
        _food(0, 13, fiber=2.5),  # Monday's lunch: 3.5 g
        _food(1, 12, fiber=2.0),
        _food(2, 12, fiber=0.0, meal_type="dinner"),
    ]

    assert lunch_no_veg_days(meals, 3.0) == 1


def test_dish_stats_groups_repeats_and_skips_placeholders():
    meals = [
        _food(0, 8, name="Phở bò", kcal=450),
        _food(1, 8, name="phở BÒ", kcal=520),
        _food(2, 8, name="Phở Bò", kcal=500),
        _food(0, 12, name="Cơm tấm", kcal=700),
        _food(1, 12, name="Cơm tấm", kcal=600),
        _food(2, 12, name="Bánh mì", kcal=400),
        _food(3, 12, name="Unknown dish"),
        _food(4, 12, name="Unknown dish"),
        _food(5, 12, name="Unnamed Food"),
        _food(6, 12, name=""),
    ]

    unique, top = dish_stats(meals)

    assert unique == 3
    assert top == (
        DishCount(name="Phở Bò", count=3, kcal=500),
        DishCount(name="Cơm tấm", count=2, kcal=650),
    )


def test_dish_stats_shows_at_most_three():
    meals = [
        _food(day, hour, name=name)
        for name, hour in (("A", 8), ("B", 12), ("C", 15), ("D", 19))
        for day in (0, 1)
    ]

    _, top = dish_stats(meals)

    assert [dish.name for dish in top] == ["A", "B", "C"]


def test_common_log_hour_needs_two_meals_and_prefers_the_earlier_hour():
    meals = [
        _food(0, 19, meal_type="dinner"),
        _food(1, 19, 45, meal_type="snack"),
        _food(2, 8, meal_type="breakfast"),
        _food(3, 8, 10, meal_type="breakfast"),
        _food(4, 12),
        _food(5, 12),  # noon: logged for a past day
    ]

    assert common_log_hour(meals) == (8, "breakfast")
    assert common_log_hour(meals[:1] + meals[4:]) == (None, None)


def test_common_log_hour_tie_on_meal_type_goes_to_the_earlier_meal():
    meals = [_food(0, 19, meal_type="snack"), _food(1, 19, meal_type="dinner")]

    assert common_log_hour(meals) == (19, "dinner")


def test_missed_meal_is_the_main_meal_most_often_missing():
    meals = [_food(day, 12) for day in range(5)] + [
        _food(day, 8, meal_type="breakfast") for day in (0, 1)
    ]

    assert missed_meal(meals, REMINDERS) == MissedMeal(
        meal_type="dinner", days=5, remind_at=1170
    )


def test_missed_meal_needs_three_missing_days():
    meals = [_food(day, 12) for day in range(2)]

    assert missed_meal(meals, REMINDERS) is None


def test_photo_share_counts_scans_and_labels():
    meals = [
        _food(0, 8, source="scanner"),
        _food(0, 12, source="food_label"),
        _food(0, 19, source="food_search"),
        _food(1, 8, source="repeat"),
        _food(1, 12, source=None),
    ]

    assert photo_share(meals) == 0.5
    assert photo_share([_food(0, 8, source=None)]) == 1.0


def test_pre_workout_carbs_averages_the_window_before_each_workout():
    meals = [
        _food(0, 16, 30, carbs=40),
        _food(0, 17, 15, carbs=20),
        _food(0, 15, 29, carbs=99),  # 91 minutes before
        _food(2, 18, carbs=99),  # after the workout began
        _food(4, 12, carbs=99),  # noon: logged for a past day
    ]
    starts = [
        (0, _local(0, 18)),
        (2, _local(2, 18)),
        (4, _local(4, 13)),
        (6, _local(6, 18)),  # no food logged on Sunday
    ]

    assert pre_workout_carbs(meals, starts, 90) == 20.0


def test_pre_workout_carbs_without_workouts_on_food_days():
    assert pre_workout_carbs([_food(0, 12)], [(1, _local(1, 18))], 90) is None


def test_food_meals_keeps_the_protein():
    [meal] = food_meals([_meal(0, 8)], MONDAY, TZ)

    assert meal.protein == 20.0


def test_low_protein_meal_is_the_main_meal_with_the_least_per_serving():
    meals = [
        *(_food(day, 8, meal_type="breakfast", protein=9.0) for day in range(3)),
        *(_food(day, 12, meal_type="lunch", protein=35.0) for day in range(3)),
        *(_food(day, 19, meal_type="dinner", protein=28.0) for day in range(2)),
        _food(4, 16, meal_type="snack", protein=2.0),  # snacks never count
    ]

    assert low_protein_meal(meals) == "breakfast"


def test_low_protein_meal_needs_two_servings_of_two_meals():
    once = [_food(0, 8, meal_type="breakfast", protein=5.0)]
    lunches = [_food(day, 12, meal_type="lunch", protein=30.0) for day in range(4)]

    assert low_protein_meal(once + lunches) is None


def test_low_protein_meal_ties_go_to_the_earlier_meal():
    meals = [
        *(_food(day, 19, meal_type="dinner", protein=20.0) for day in range(2)),
        *(_food(day, 12, 30, meal_type="lunch", protein=20.0) for day in range(2)),
    ]

    assert low_protein_meal(meals) == "lunch"


def test_weekend_peaks_are_the_biggest_meal_of_fri_to_sun():
    meals = [
        _food(3, 19, name="Cơm gà", kcal=1500.0),  # Thursday
        _food(4, 12, name="Bún chả", kcal=700.0),
        _food(4, 19, name="Lẩu Thái", kcal=1250.4),
        # An unnamed scan is still Saturday's biggest meal: it keeps no
        # name rather than letting the smaller Phở bò stand in for it.
        _food(5, 20, name="Unknown dish", kcal=2000.0),
        _food(5, 12, name="Phở bò", kcal=600.0),
    ]

    assert weekend_peaks(meals) == (
        PeakMeal(4, "Lẩu Thái", 1250),
        PeakMeal(5, "", 2000),
    )


def test_low_protein_meal_counts_a_meal_logged_in_parts_as_one_meal():
    # Lunch is logged as rice, soup and meat each day: 30 g in all, more than
    # breakfast's 20 g, though each part alone has less.
    lunch = [
        _food(day, 12, minute, meal_type="lunch", protein=grams)
        for day in range(3)
        for minute, grams in ((0, 4.0), (5, 6.0), (10, 20.0))
    ]
    breakfast = [_food(day, 8, meal_type="breakfast", protein=20.0) for day in range(3)]

    assert low_protein_meal(lunch + breakfast) == "breakfast"


def test_low_protein_meal_needs_the_meal_on_two_days():
    # Two lunch items on one day are one lunch.
    meals = [
        _food(0, 12, 0, meal_type="lunch", protein=2.0),
        _food(0, 12, 5, meal_type="lunch", protein=2.0),
        *(_food(day, 8, meal_type="breakfast", protein=20.0) for day in range(3)),
    ]

    assert low_protein_meal(meals) is None


def test_low_protein_meal_leaves_out_meals_logged_later():
    # Backdated meals are stamped at noon, so their "lunch" says nothing.
    meals = [
        *(_food(day, 12, meal_type="lunch", protein=2.0) for day in range(3)),
        *(_food(day, 8, meal_type="breakfast", protein=20.0) for day in range(3)),
        *(_food(day, 19, meal_type="dinner", protein=30.0) for day in range(3)),
    ]

    assert low_protein_meal(meals) == "breakfast"
