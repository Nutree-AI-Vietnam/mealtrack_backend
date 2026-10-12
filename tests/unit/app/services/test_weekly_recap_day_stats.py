"""Weekly recap day numbers read from the progress summary rows."""

from datetime import date, timedelta

import pytest

from src.app.services.weekly_recap_day_stats import day_stats

MONDAY = date(2026, 10, 5)


def _row(day: int, **values) -> dict:
    row = {
        "date": (MONDAY + timedelta(days=day)).isoformat(),
        "calories": 1800.0,
        "protein_g": 110.0,
        "fiber_g": 20.0,
        "fiber_target_g": 28.0,
        "target_calories": 2000,
        "hydration_ml": 1500,
        "hydration_goal_ml": 2500,
        "protein_target_g": 120.0,
        "is_cheat_day": False,
    }
    return row | values


def test_day_stats_reads_each_days_numbers():
    rows = [_row(day, calories=1500.0 + day * 100) for day in range(7)]
    rows[5] |= {"is_cheat_day": True, "target_calories": 2300}

    days = day_stats(rows, MONDAY, food_days={0, 1, 2, 3, 4, 5, 6})

    assert days is not None
    assert days.kcal == (1500.0, 1600.0, 1700.0, 1800.0, 1900.0, 2000.0, 2100.0)
    assert days.protein == (110.0,) * 7
    assert days.water == (1.5,) * 7
    assert days.budgets == (2000.0,) * 5 + (2300.0, 2000.0)
    assert days.budget == 2043  # mean of the seven targets
    assert days.cheat_days == (5,)
    assert (days.protein_target, days.water_target) == (120.0, 2.5)
    assert (days.fiber_avg, days.fiber_target) == (20.0, 28.0)


def test_day_stats_counts_food_only_on_days_with_a_meal():
    rows = [_row(day, fiber_g=10.0 * day) for day in range(7)]

    days = day_stats(rows, MONDAY, food_days={1, 3})

    assert days is not None
    assert days.kcal == (0.0, 1800.0, 0.0, 1800.0, 0.0, 0.0, 0.0)
    assert days.protein == (0.0, 110.0, 0.0, 110.0, 0.0, 0.0, 0.0)
    assert days.water == (1.5,) * 7  # water counts every day
    assert days.fiber_avg == 20.0


def test_day_stats_before_sign_up_takes_the_weeks_average_target():
    rows = [_row(day, target_calories=1900 + day * 50) for day in (4, 5, 6)]
    rows[-1] |= {"protein_target_g": 135.0, "hydration_goal_ml": 2000}

    days = day_stats(rows, MONDAY, food_days={4, 5, 6})

    assert days is not None
    assert days.budget == 2150
    assert days.budgets == (2150.0,) * 4 + (2100.0, 2150.0, 2200.0)
    assert days.kcal[:4] == (0.0,) * 4
    assert days.water[:4] == (0.0,) * 4
    assert (days.protein_target, days.water_target) == (135.0, 2.0)


def test_day_stats_gives_a_day_without_a_target_the_average():
    rows = [_row(0, target_calories=0), _row(1, target_calories=None), _row(2)]

    days = day_stats(rows, MONDAY, food_days={0, 1, 2})

    assert days is not None
    assert days.budgets == (2000.0,) * 7


def test_day_stats_rounds_the_fiber_target():
    rows = [_row(0, fiber_target_g=25.0), _row(1, fiber_target_g=26.0)]

    days = day_stats(rows, MONDAY, food_days={0})

    assert days is not None
    assert days.fiber_target == 26.0  # 25.5 rounds to even


def test_day_stats_skips_rows_it_cannot_place():
    rows = [
        _row(-1, calories=9999.0),
        _row(7, calories=9999.0),
        {"calories": 9999.0},
        {"date": "not a date", "calories": 9999.0},
        _row(0, calories=True, protein_g="12"),
    ]

    days = day_stats(rows, MONDAY, food_days={0})

    assert days is not None
    assert days.kcal == (0.0,) * 7
    assert days.protein == (0.0,) * 7


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [_row(0, target_calories=0)],
        [_row(0, target_calories=-50)],
        [_row(0, target_calories=0.4)],
    ],
)
def test_day_stats_needs_a_calorie_target(rows):
    assert day_stats(rows, MONDAY, food_days={0}) is None
