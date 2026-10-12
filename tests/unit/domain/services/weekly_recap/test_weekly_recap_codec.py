"""WeekStats snapshots survive a JSON round trip and schema changes."""

from __future__ import annotations

import json
from datetime import date

from src.domain.model.weekly_recap import (
    DishCount,
    MissedMeal,
    WeekStats,
    Workout,
    week_stats_from_dict,
    week_stats_to_dict,
)

WEEK = (3, 3, 2, 3, 3, 2, 3)
FULL = WeekStats(
    week_start=date(2023, 10, 2),
    week_no=14,
    account_days=120,
    goal="cut",
    budget=1900,
    protein_target=110.0,
    meals_by_day=WEEK,
    kcal_by_day=(1820.0, 1790.0, 1650.0, 1880.0, 1860.0, 2140.0, 1740.0),
    protein_by_day=(118.0, 112.0, 96.0, 121.0, 115.0, 104.0, 118.0),
    water_by_day=(2.4, 1.6, 2.6, 1.8, 1.5, 1.4, 2.5),
    fiber_avg=14.2,
    streak=7,
    budget_by_day=(1900.0,) * 7,
    workouts=(Workout(0, 35, 380), Workout(5, 90, 570, strength=True)),
    late_snack_days=(1, 3),
    top_dishes=(DishCount("Phở bò", 3, 450),),
    common_log_hour=12,
    common_meal="lunch",
    missed_meal=MissedMeal("breakfast", 5, 510),
    pre_workout_carbs=30.5,
    progress_pct=0.53,
    progress_delta=-0.02,
    cheat_days=(5,),
)


def test_round_trips_through_json() -> None:
    encoded = json.loads(json.dumps(week_stats_to_dict(FULL)))

    assert week_stats_from_dict(encoded) == FULL


def test_encodes_plain_values() -> None:
    encoded = week_stats_to_dict(FULL)

    assert encoded["week_start"] == "2023-10-02"
    assert encoded["meals_by_day"] == list(WEEK)
    assert encoded["workouts"][1] == {
        "day": 5,
        "minutes": 90,
        "kcal": 570,
        "strength": True,
    }
    assert encoded["missed_meal"] == {
        "meal_type": "breakfast",
        "days": 5,
        "remind_at": 510,
    }


def test_old_snapshots_load_with_defaults_and_ignore_unknown_keys() -> None:
    encoded = week_stats_to_dict(FULL)
    for key in ("cheat_days", "budget_by_day", "missed_meal", "pre_workout_carbs"):
        del encoded[key]
    encoded["mood"] = "great"
    encoded["workouts"][0]["source"] = "apple_health"

    stats = week_stats_from_dict(encoded)

    assert stats.cheat_days == ()
    assert stats.budget_by_day is None
    assert stats.missed_meal is None
    assert stats.pre_workout_carbs is None
    assert stats.workouts == FULL.workouts


def test_old_missed_meal_loads_without_a_reminder_time() -> None:
    encoded = week_stats_to_dict(FULL)
    del encoded["missed_meal"]["remind_at"]

    assert week_stats_from_dict(encoded).missed_meal == MissedMeal("breakfast", 5)
