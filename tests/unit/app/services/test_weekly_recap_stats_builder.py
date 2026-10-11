"""Turning one week's logs and summary rows into the recap engine's WeekStats."""

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.app.services.weekly_recap_meal_stats import FoodMeal
from src.app.services.weekly_recap_stats_builder import build_week_stats
from src.app.services.weekly_recap_week_logs import WeekLogs
from src.domain.model.movement.movement_entry import MovementEntry
from src.domain.model.weekly_recap import (
    DishCount,
    MissedMeal,
    WeekStats,
    Workout,
    week_stats_from_dict,
    week_stats_to_dict,
)
from src.domain.model.weight.weight_entry import WeightEntry
from src.domain.services.weekly_recap import RecapConfig, generate_recap

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MONDAY = date(2026, 10, 5)
USER = "11111111-1111-1111-1111-111111111111"
CONFIG = RecapConfig()
KCAL = (1800.0, 1900.0, 2100.0, 1700.0, 2400.0, 2600.0, 1500.0)


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(MONDAY + timedelta(days=day), time(hour, minute), TZ)


def _food(
    day: int,
    hour: int,
    minute: int = 0,
    *,
    meal_type: str,
    name: str,
    carbs: float = 50.0,
) -> FoodMeal:
    return FoodMeal(
        day=day,
        logged_at=_local(day, hour, minute),
        meal_type=meal_type,
        name=name,
        kcal=500.0,
        carbs=carbs,
        fiber=2.0,
        source="scanner",
    )


def _move(day: int, hour: int, minutes: int, kcal: float, activity_id: str):
    return MovementEntry(
        user_id=USER,
        activity_name="Workout",
        duration_min=minutes,
        kcal_burned=kcal,
        intensity="moderate",
        logged_at=_local(day, hour).astimezone(UTC),
        activity_id=activity_id,
    )


def _weigh(day: int, kg: float) -> WeightEntry:
    return WeightEntry(user_id=USER, weight_kg=kg, recorded_at=_local(day, 7))


def _row(day: int, calories: float, **values) -> dict:
    row = {
        "date": (MONDAY + timedelta(days=day)).isoformat(),
        "calories": calories,
        "protein_g": 110.0,
        "fiber_g": 20.0,
        "fiber_target_g": 28.0,
        "target_calories": 2000,
        "hydration_ml": 2500,
        "hydration_goal_ml": 2500,
        "protein_target_g": 120.0,
        "is_cheat_day": False,
    }
    return row | values


def _week_logs(**overrides) -> WeekLogs:
    meals = (
        *(_food(day, 8, meal_type="breakfast", name="Bánh mì") for day in range(5)),
        *(_food(day, 12, 30, meal_type="lunch", name="Phở bò") for day in range(7)),
        _food(2, 16, 30, meal_type="snack", name="Chuối", carbs=60.0),
        _food(4, 22, 30, meal_type="snack", name="Chè"),
    )
    values = {
        "week_start": MONDAY,
        "tz": TZ,
        "created": date(2026, 9, 1),
        "goal": "cut",
        "profile": SimpleNamespace(
            weight_kg=80.0, target_weight_kg=70.0, goal_start_weight_kg=None
        ),
        "meals": tuple(sorted(meals, key=lambda meal: meal.logged_at)),
        "movements": (
            _move(2, 18, 45, 300.0, "gym_strength"),
            _move(5, 7, 30, 200.0, "running"),
        ),
        "drinks": (),
        "weights_this_week": (_weigh(3, 78.0),),
        "weights_last_week": (_weigh(-4, 79.0),),
        "log_dates": tuple(MONDAY + timedelta(days=day) for day in range(6, -4, -1)),
        "case_counts": {"perfect": 2, "first_week": 1},
        "reminders": {"breakfast": 450, "lunch": 720, "dinner": 1170},
    }
    return WeekLogs(**(values | overrides))


def test_build_week_stats_combines_the_logs_and_the_summary_rows():
    rows = [_row(day, kcal) for day, kcal in enumerate(KCAL)]
    rows[5]["is_cheat_day"] = True

    stats = build_week_stats(_week_logs(), rows, CONFIG)

    assert stats == WeekStats(
        week_start=MONDAY,
        week_no=6,
        account_days=41,
        goal="cut",
        budget=2000,
        protein_target=120.0,
        meals_by_day=(2, 2, 3, 2, 3, 1, 1),
        kcal_by_day=KCAL,
        protein_by_day=(110.0,) * 7,
        water_by_day=(2.5,) * 7,
        fiber_avg=20.0,
        streak=10,
        budget_by_day=(2000.0,) * 7,
        fiber_target=28.0,
        water_target=2.5,
        week_days=7,
        gap_before=0,
        lunch_no_veg_days=7,
        workouts=(Workout(2, 45, 300, True), Workout(5, 30, 200, False)),
        late_snack_days=(4,),
        late_snack_kcal=500,
        streak_is_record=False,
        workouts_is_record=False,
        photo_share=1.0,
        unique_dishes=4,
        top_dishes=(DishCount("Phở bò", 7, 500), DishCount("Bánh mì", 5, 500)),
        common_log_hour=12,
        common_meal="lunch",
        missed_meal=MissedMeal("dinner", 7, remind_at=1170),
        # Saturday is a cheat day, so only Friday and Sunday count here.
        weekend_delta=-50.0,
        under_days=(3, 6),
        training_day_kcal=2100.0,
        pre_workout_carbs=30.0,  # 60 g before Wednesday's, none before Saturday's
        progress_pct=0.2,
        progress_delta=0.1,
        perfect_weeks_before=2,
        had_first_week=True,
        cheat_days=(5,),
    )


def test_build_week_stats_for_a_mid_week_sign_up():
    created = date(2026, 10, 7)  # Wednesday
    days = range(2, 7)
    logs = _week_logs(
        created=created,
        goal="recomp",
        profile=None,
        meals=tuple(
            _food(day, hour, meal_type=meal_type, name="Phở bò")
            for day in days
            for hour, meal_type in ((8, "breakfast"), (12, "lunch"))
        ),
        movements=(),
        weights_this_week=(),
        weights_last_week=(),
        log_dates=tuple(MONDAY + timedelta(days=day) for day in days),
        case_counts={},
    )
    rows = [_row(day, 1900.0, target_calories=1700 + day * 50) for day in days]

    stats = build_week_stats(logs, rows, CONFIG)

    assert stats is not None
    assert (stats.week_no, stats.account_days, stats.week_days) == (1, 5, 5)
    assert (stats.streak, stats.gap_before) == (5, 0)
    assert stats.budget_by_day == (
        1900.0,
        1900.0,
        1800.0,
        1850.0,
        1900.0,
        1950.0,
        2000.0,
    )
    assert (stats.progress_pct, stats.progress_delta) == (None, None)
    assert (stats.perfect_weeks_before, stats.had_first_week) == (0, False)
    assert generate_recap(stats, CONFIG, "vi")["case_id"] == "first_week"


def test_build_week_stats_needs_a_calorie_target():
    rows = [_row(day, 1800.0, target_calories=0) for day in range(7)]

    assert build_week_stats(_week_logs(), rows, CONFIG) is None


def test_built_week_stats_can_be_stored_and_rendered():
    rows = [_row(day, kcal) for day, kcal in enumerate(KCAL)]
    stats = build_week_stats(_week_logs(), rows, CONFIG)
    assert stats is not None

    restored = week_stats_from_dict(week_stats_to_dict(stats))
    payload = generate_recap(restored, CONFIG, "en")

    assert restored == stats
    assert payload == generate_recap(stats, CONFIG, "en")
    assert payload["week_start"] == "2026-10-05"
    assert payload["slides"][0] == "intro"
