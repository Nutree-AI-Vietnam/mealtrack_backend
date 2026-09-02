"""Pure cache invalidation operation builders."""

from __future__ import annotations

from datetime import date, timedelta

from src.domain.cache.cache_keys import CacheKeys

DELETE_KEY = "delete_key"
DELETE_PATTERN = "delete_pattern"


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _progress_summary_pattern(user_id: str) -> dict[str, str]:
    return {
        "op": DELETE_PATTERN,
        "pattern": f"user:{user_id}:progress_summary:*",
    }


def build_meal_invalidation_operations(
    user_id: str,
    meal_date: date,
    *,
    current_date: date | None = None,
) -> list[dict[str, str]]:
    """Build the exact cache deletes currently used after a meal write."""
    meal_week_start = _week_start(meal_date)
    current_week_start = _week_start(current_date or date.today())

    operations: list[dict[str, str]] = [
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:activities:{meal_date.isoformat()}:*",
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_macros(user_id, meal_date)[0],
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.weekly_budget(user_id, meal_week_start)[0],
        },
        {
            "op": DELETE_PATTERN,
            "pattern": CacheKeys.weekly_budget_pattern(user_id, meal_week_start),
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:nutrition_bulk:*",
        },
        _progress_summary_pattern(user_id),
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_breakdown(user_id, meal_week_start)[0],
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.user_streak(user_id)[0],
        },
    ]

    if meal_week_start != current_week_start:
        operations.extend(
            [
                {
                    "op": DELETE_KEY,
                    "key": CacheKeys.weekly_budget(user_id, current_week_start)[0],
                },
                {
                    "op": DELETE_PATTERN,
                    "pattern": CacheKeys.weekly_budget_pattern(
                        user_id, current_week_start
                    ),
                },
                {
                    "op": DELETE_KEY,
                    "key": CacheKeys.daily_breakdown(user_id, current_week_start)[0],
                },
            ]
        )

    return operations


def build_hydration_invalidation_operations(
    user_id: str,
    log_date: date,
    *,
    current_date: date | None = None,
) -> list[dict[str, str]]:
    """Build cache invalidation operations after a hydration write."""
    log_week_start = _week_start(log_date)
    current_week_start = _week_start(current_date or date.today())

    operations: list[dict[str, str]] = [
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:activities:{log_date.isoformat()}:*",
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:hydration:{log_date.isoformat()}:*",
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_macros(user_id, log_date)[0],
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.weekly_budget(user_id, log_week_start)[0],
        },
        {
            "op": DELETE_PATTERN,
            "pattern": CacheKeys.weekly_budget_pattern(user_id, log_week_start),
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:nutrition_bulk:*",
        },
        _progress_summary_pattern(user_id),
        {
            "op": DELETE_KEY,
            "key": CacheKeys.weekly_hydration(user_id, log_week_start)[0],
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_breakdown(user_id, log_week_start)[0],
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.user_streak(user_id)[0],
        },
    ]

    if log_week_start != current_week_start:
        operations.extend(
            [
                {
                    "op": DELETE_KEY,
                    "key": CacheKeys.weekly_budget(user_id, current_week_start)[0],
                },
                {
                    "op": DELETE_PATTERN,
                    "pattern": CacheKeys.weekly_budget_pattern(
                        user_id, current_week_start
                    ),
                },
                {
                    "op": DELETE_KEY,
                    "key": CacheKeys.daily_breakdown(user_id, current_week_start)[0],
                },
            ]
        )

    return operations


def build_movement_invalidation_operations(
    user_id: str,
    log_date: date,
    *,
    current_date: date | None = None,
) -> list[dict[str, str]]:
    """Build cache invalidation operations after a movement write."""
    log_week_start = _week_start(log_date)
    current_week_start = _week_start(current_date or date.today())

    operations: list[dict[str, str]] = [
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:activities:{log_date.isoformat()}:*",
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_macros(user_id, log_date)[0],
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.weekly_budget(user_id, log_week_start)[0],
        },
        {
            "op": DELETE_PATTERN,
            "pattern": CacheKeys.weekly_budget_pattern(user_id, log_week_start),
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:nutrition_bulk:*",
        },
        _progress_summary_pattern(user_id),
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_breakdown(user_id, log_week_start)[0],
        },
    ]

    if log_week_start != current_week_start:
        operations.extend(
            [
                {
                    "op": DELETE_KEY,
                    "key": CacheKeys.weekly_budget(user_id, current_week_start)[0],
                },
                {
                    "op": DELETE_PATTERN,
                    "pattern": CacheKeys.weekly_budget_pattern(
                        user_id, current_week_start
                    ),
                },
                {
                    "op": DELETE_KEY,
                    "key": CacheKeys.daily_breakdown(user_id, current_week_start)[0],
                },
            ]
        )

    return operations


def build_profile_invalidation_operations(user_id: str) -> list[dict[str, str]]:
    """Build cache invalidation operations after a user profile or targets write."""
    return [
        {
            "op": DELETE_KEY,
            "key": CacheKeys.user_tdee(user_id)[0],
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.user_profile(user_id)[0],
        },
        {
            "op": DELETE_KEY,
            "key": CacheKeys.user_metrics(user_id)[0],
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:macros:*",
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:nutrition_bulk:*",
        },
        _progress_summary_pattern(user_id),
        {
            "op": DELETE_PATTERN,
            "pattern": CacheKeys.weekly_budget_user_pattern(user_id),
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:daily_breakdown:*",
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:hydration:*",
        },
        {
            "op": DELETE_PATTERN,
            "pattern": f"user:{user_id}:activities:*",
        },
    ]


def build_cheat_day_invalidation_operations(
    user_id: str, cheat_day: date
) -> list[dict[str, str]]:
    """Build cache invalidation operations after a cheat day write."""
    cheat_week_start = _week_start(cheat_day)
    return [
        {
            "op": DELETE_KEY,
            "key": CacheKeys.weekly_budget(user_id, cheat_week_start)[0],
        },
        {
            "op": DELETE_PATTERN,
            "pattern": CacheKeys.weekly_budget_pattern(user_id, cheat_week_start),
        },
        _progress_summary_pattern(user_id),
    ]


def build_saved_suggestion_invalidation_operations(
    user_id: str,
) -> list[dict[str, str]]:
    """Build cache invalidation operations after a saved suggestion write."""
    return [
        {
            "op": DELETE_KEY,
            "key": CacheKeys.saved_suggestions(user_id)[0],
        },
    ]
