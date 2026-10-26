"""Cache invalidations a write applies before it returns.

The meal, hydration and movement builders run on the API write path in every
environment, so reads after a write never wait on the nutreeai_async Queue
worker. They name exact keys only; range-keyed caches are fenced by bumping
``CacheKeys.intake_revision`` instead of a SCAN. The worker's purge
(`src/domain/cache/cache-invalidation-builders.ts`) is a second pass.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.domain.cache.cache_keys import CacheKeys

DELETE_KEY = "delete_key"
DELETE_PATTERN = "delete_pattern"
BUMP_REVISION = "bump_revision"

# Supported languages from AcceptLanguageMiddleware — used to enumerate
# language-suffixed cache keys deterministically instead of SCAN.
SUPPORTED_LANGUAGES = ("en", "vi", "es", "fr", "de", "ja", "zh")


def _expand_language_pattern(prefix: str) -> list[dict[str, str]]:
    """Expand a language-suffixed pattern into deterministic key deletes."""
    return [{"op": DELETE_KEY, "key": f"{prefix}{lang}"} for lang in SUPPORTED_LANGUAGES]


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _weekly_budget_deletes(user_id: str, week_start: date) -> list[dict[str, str]]:
    """The week key and one per target date, since target dates share a week."""
    return [
        {"op": DELETE_KEY, "key": CacheKeys.weekly_budget(user_id, week_start)[0]},
        *(
            {
                "op": DELETE_KEY,
                "key": CacheKeys.weekly_budget(
                    user_id, week_start, week_start + timedelta(days=offset)
                )[0],
            }
            for offset in range(7)
        ),
    ]


def _intake_revision_bump(user_id: str) -> dict[str, str]:
    key, ttl = CacheKeys.intake_revision(user_id)
    return {"op": BUMP_REVISION, "key": key, "ttl": str(ttl)}


def _progress_summary_pattern(user_id: str) -> dict[str, str]:
    return {
        "op": DELETE_PATTERN,
        "pattern": f"user:{user_id}:progress_summary:*",
    }


def _progress_recap_pattern(user_id: str) -> dict[str, str]:
    return {
        "op": DELETE_PATTERN,
        "pattern": f"user:{user_id}:progress_recap:*",
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
        *_expand_language_pattern(f"user:{user_id}:activities:{meal_date.isoformat()}:"),
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_macros(user_id, meal_date)[0],
        },
        *_weekly_budget_deletes(user_id, meal_week_start),
        _intake_revision_bump(user_id),
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
                *_weekly_budget_deletes(user_id, current_week_start),
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
        *_expand_language_pattern(f"user:{user_id}:activities:{log_date.isoformat()}:"),
        *_expand_language_pattern(f"user:{user_id}:hydration:{log_date.isoformat()}:"),
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_macros(user_id, log_date)[0],
        },
        *_weekly_budget_deletes(user_id, log_week_start),
        _intake_revision_bump(user_id),
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
                *_weekly_budget_deletes(user_id, current_week_start),
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
        *_expand_language_pattern(f"user:{user_id}:activities:{log_date.isoformat()}:"),
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_macros(user_id, log_date)[0],
        },
        *_weekly_budget_deletes(user_id, log_week_start),
        _intake_revision_bump(user_id),
        {
            "op": DELETE_KEY,
            "key": CacheKeys.daily_breakdown(user_id, log_week_start)[0],
        },
    ]

    if log_week_start != current_week_start:
        operations.extend(
            [
                *_weekly_budget_deletes(user_id, current_week_start),
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
        _progress_recap_pattern(user_id),
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
        _progress_recap_pattern(user_id),
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
