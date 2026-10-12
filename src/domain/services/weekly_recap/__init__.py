"""Weekly recap ("Weekly Wrapped"): the story engine and its week math."""

from src.domain.services.weekly_recap.config import RecapConfig
from src.domain.services.weekly_recap.engine import generate_recap
from src.domain.services.weekly_recap.stats_math import (
    account_days,
    goal_progress,
    latest_week_start,
    meal_for_hour,
    monday_of,
    streak_stats,
    training_day_kcal,
    under_days,
    week_days,
    week_number,
    weekend_delta,
)

__all__ = [
    "RecapConfig",
    "account_days",
    "generate_recap",
    "goal_progress",
    "latest_week_start",
    "meal_for_hour",
    "monday_of",
    "streak_stats",
    "training_day_kcal",
    "under_days",
    "week_days",
    "week_number",
    "weekend_delta",
]
