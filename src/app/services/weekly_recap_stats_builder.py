"""Turn one week's logs into the WeekStats the recap engine reads.

Calories, protein, water, and targets come from the progress summary rows,
so they match what the user saw each day; the rest comes from the logs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.app.services.weekly_recap_activity_stats import (
    strength_record,
    sugary_drinks,
    weight_progress,
    workout_starts,
    workouts,
)
from src.app.services.weekly_recap_day_stats import day_stats
from src.app.services.weekly_recap_meal_stats import (
    common_log_hour,
    dish_stats,
    late_snacks,
    lunch_no_veg_days,
    meals_by_day,
    missed_meal,
    photo_share,
    pre_workout_carbs,
)
from src.app.services.weekly_recap_week_logs import WeekLogs
from src.domain.model.weekly_recap import WeekStats
from src.domain.services.weekly_recap import (
    RecapConfig,
    account_days,
    streak_stats,
    training_day_kcal,
    under_days,
    week_days,
    week_number,
    weekend_delta,
)


def build_week_stats(
    logs: WeekLogs, rows: Sequence[Mapping[str, Any]], config: RecapConfig
) -> WeekStats | None:
    """The week's numbers for the recap engine; None without a calorie target."""
    meals = logs.meals
    food_days = {meal.day for meal in meals}
    days = day_stats(rows, logs.week_start, food_days)
    if days is None:
        return None
    counted = [day for day in sorted(food_days) if day not in days.cheat_days]
    moves = workouts(logs.movements, logs.week_start, logs.tz)
    starts = workout_starts(logs.movements, logs.week_start, logs.tz)
    streak, gap, streak_record = streak_stats(
        logs.log_dates, logs.week_start, logs.created
    )
    snack_days, snack_kcal = late_snacks(meals, config.late_snack_hour)
    unique, top = dish_stats(meals)
    hour, meal_type = common_log_hour(meals)
    sugary, sugary_kcal = sugary_drinks(logs.drinks, config.sugary_min_sugar_g)
    progress, progress_delta = weight_progress(
        logs.goal, logs.profile, logs.weights_this_week, logs.weights_last_week
    )
    return WeekStats(
        week_start=logs.week_start,
        week_no=week_number(logs.week_start, logs.created),
        account_days=account_days(logs.week_start, logs.created),
        goal=logs.goal,
        budget=days.budget,
        protein_target=days.protein_target,
        meals_by_day=meals_by_day(meals),
        kcal_by_day=days.kcal,
        protein_by_day=days.protein,
        water_by_day=days.water,
        fiber_avg=days.fiber_avg,
        streak=streak,
        budget_by_day=days.budgets,
        fiber_target=days.fiber_target,
        water_target=days.water_target,
        week_days=week_days(logs.week_start, logs.created),
        gap_before=gap,
        lunch_no_veg_days=lunch_no_veg_days(meals, config.lunch_fiber_veg_g),
        workouts=moves,
        late_snack_days=snack_days,
        late_snack_kcal=snack_kcal,
        streak_is_record=streak_record,
        workouts_is_record=strength_record(logs.movements, logs.week_start, logs.tz),
        photo_share=photo_share(meals),
        unique_dishes=unique,
        top_dishes=top,
        common_log_hour=hour,
        common_meal=meal_type,
        missed_meal=missed_meal(meals, logs.reminders),
        sugary_drinks=sugary,
        sugary_kcal=sugary_kcal,
        weekend_delta=weekend_delta(days.kcal, days.budgets, counted),
        under_days=under_days(days.kcal, days.budgets, counted, config.under_ratio),
        training_day_kcal=training_day_kcal(
            days.kcal, counted, (move.day for move in moves)
        ),
        pre_workout_carbs=pre_workout_carbs(
            meals, starts, config.pre_workout_window_min
        ),
        progress_pct=progress,
        progress_delta=progress_delta,
        perfect_weeks_before=logs.case_counts.get("perfect", 0),
        had_first_week=logs.case_counts.get("first_week", 0) > 0,
        cheat_days=days.cheat_days,
    )
