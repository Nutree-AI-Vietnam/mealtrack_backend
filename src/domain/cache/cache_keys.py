"""
Shared cache key definitions and TTL helpers.
"""

from __future__ import annotations

from datetime import date

from src.domain.services.nutrition_integrity_policy import (
    NUTRITION_INTEGRITY_POLICY_VERSION,
)


class CacheKeys:
    """Centralized cache key generator with TTL policies."""

    # Bumped when search-time catalog adoption changes what a cached search
    # result contains (e.g. adding food_reference_id to provider hits), so
    # stale pre-adoption entries expire instead of serving thin ids forever.
    CATALOG_ADOPT_CACHE_VERSION = "catalog_adopt_v1"

    TTL_10_MIN = 600
    TTL_5_MIN = 300
    TTL_30_MIN = 1800
    TTL_1_HOUR = 3600
    TTL_1_DAY = 86400
    TTL_7_DAYS = 604_800
    TTL_30_DAYS = 2_592_000

    @staticmethod
    def user_profile(user_id: str) -> tuple[str, int]:
        return (f"user:profile:{user_id}", CacheKeys.TTL_30_DAYS)

    @staticmethod
    def auth_uid_to_user(firebase_uid: str) -> tuple[str, int]:
        """Cache key for Firebase UID -> active database user mapping."""
        return (f"auth:uid:{firebase_uid}", CacheKeys.TTL_10_MIN)

    @staticmethod
    def user_tdee(user_id: str) -> tuple[str, int]:
        """Cache key for user TDEE calculation. 24h TTL."""
        return (f"user:tdee_v2:{user_id}", CacheKeys.TTL_1_DAY)

    @staticmethod
    def daily_macros(user_id: str, target_date: date) -> tuple[str, int]:
        return (
            f"user:{user_id}:macros:{target_date.isoformat()}",
            CacheKeys.TTL_1_DAY,
        )

    @staticmethod
    def daily_breakdown(user_id: str, week_start_date: date) -> tuple[str, int]:
        return (
            f"user:{user_id}:daily_breakdown:{week_start_date.isoformat()}",
            CacheKeys.TTL_30_MIN,
        )

    @staticmethod
    def weekly_budget(
        user_id: str, week_start_date: date, target_date: date | None = None
    ) -> tuple[str, int]:
        """Cache key for weekly budget responses. 30 min TTL.

        The stored weekly budget is week-scoped, but the API response contains
        target-date-specific fields such as remaining_days and adjusted targets.
        """
        date_suffix = (
            week_start_date.isoformat()
            if target_date is None
            else f"{week_start_date.isoformat()}:{target_date.isoformat()}"
        )
        return (
            f"user:{user_id}:weekly_budget_v4:{date_suffix}",
            CacheKeys.TTL_30_MIN,
        )

    @staticmethod
    def weekly_budget_pattern(user_id: str, week_start_date: date) -> str:
        return f"user:{user_id}:weekly_budget_v4:{week_start_date.isoformat()}:*"

    @staticmethod
    def weekly_budget_user_pattern(user_id: str) -> str:
        return f"user:{user_id}:weekly_budget_v4:*"

    @staticmethod
    def nutrition_bulk(
        user_id: str, start_date: date, end_date: date
    ) -> tuple[str, int]:
        """Cache key for a bulk-nutrition date range. Short TTL.

        The cached response embeds a weekly summary computed relative to the
        current day, so the TTL is deliberately short to bound day-rollover
        drift and any target change (weight/metrics) not on the high-frequency
        write paths. Meal/movement/hydration/custom-macro writes purge these
        keys synchronously (see CacheInvalidationService) for instant freshness.
        """
        return (
            f"user:{user_id}:nutrition_bulk:{start_date.isoformat()}:{end_date.isoformat()}",
            CacheKeys.TTL_5_MIN,
        )

    @staticmethod
    def food_search(
        query: str,
        *,
        policy_version: str | None = None,
        generation: int | None = None,
    ) -> tuple[str, int]:
        normalized = query.lower().strip()[:64]
        policy = policy_version or NUTRITION_INTEGRITY_POLICY_VERSION
        if generation is None:
            return (
                f"food:search:v2:{policy}:{normalized}",
                CacheKeys.TTL_7_DAYS,
            )
        return (
            f"food:search:v3:{CacheKeys.CATALOG_ADOPT_CACHE_VERSION}:"
            f"{policy}:generation:{generation}:{normalized}",
            CacheKeys.TTL_7_DAYS,
        )

    @staticmethod
    def food_details(food_id: str) -> tuple[str, int]:
        return (f"food:details:{food_id}", CacheKeys.TTL_7_DAYS)

    @staticmethod
    def feature_flag(flag_name: str) -> tuple[str, int]:
        return (f"feature:flag:{flag_name}", CacheKeys.TTL_10_MIN)

    @staticmethod
    def feature_flags() -> tuple[str, int]:
        return ("feature:flags:all", CacheKeys.TTL_10_MIN)

    @staticmethod
    def user_streak(user_id: str) -> tuple[str, int]:
        """Cache key for user streak. 1h TTL."""
        return (f"user:streak:{user_id}", CacheKeys.TTL_1_HOUR)

    @staticmethod
    def daily_activities(
        user_id: str, target_date: date, language: str = "en"
    ) -> tuple[str, int]:
        """Cache key for daily activities list. 5min TTL. Includes language to prevent cross-language cache pollution."""
        return (
            f"user:{user_id}:activities:{target_date.isoformat()}:{language}",
            CacheKeys.TTL_5_MIN,
        )

    @staticmethod
    def daily_hydration(
        user_id: str, target_date: date, language: str = "en"
    ) -> tuple[str, int]:
        """Cache key for daily hydration summary. 5min TTL."""
        return (
            f"user:{user_id}:hydration:{target_date.isoformat()}:{language}",
            CacheKeys.TTL_5_MIN,
        )

    @staticmethod
    def weekly_hydration(user_id: str, week_start: date) -> tuple[str, int]:
        """Cache key for weekly hydration chart. 5min TTL."""
        return (
            f"user:{user_id}:hydration_weekly:{week_start.isoformat()}",
            CacheKeys.TTL_5_MIN,
        )

    @staticmethod
    def saved_suggestions(user_id: str) -> tuple[str, int]:
        """Cache key for user's saved suggestions. 1h TTL."""
        return (f"user:{user_id}:saved_suggestions", CacheKeys.TTL_1_HOUR)

    @staticmethod
    def user_metrics(user_id: str) -> tuple[str, int]:

        """Cache key for user metrics (age, weight, height, goals). 24h TTL."""
        return (f"user:{user_id}:metrics", CacheKeys.TTL_1_DAY)

    @staticmethod
    def pattern_for_user(user_id: str) -> str:
        return f"user:{user_id}:*"
