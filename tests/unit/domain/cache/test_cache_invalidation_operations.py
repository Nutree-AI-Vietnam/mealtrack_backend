from datetime import date

from src.domain.cache.cache_invalidation_operations import (
    DELETE_KEY,
    DELETE_PATTERN,
    build_meal_invalidation_operations,
)
from src.domain.cache.cache_keys import CacheKeys


def test_meal_operations_preserve_current_week_cache_coverage() -> None:
    operations = build_meal_invalidation_operations(
        "user1",
        date(2026, 6, 2),
        current_date=date(2026, 6, 3),
    )

    assert {operation["op"] for operation in operations} == {
        DELETE_KEY,
        DELETE_PATTERN,
    }
    assert {operation.get("key") for operation in operations} >= {
        CacheKeys.daily_macros("user1", date(2026, 6, 2))[0],
        CacheKeys.weekly_budget("user1", date(2026, 6, 1))[0],
        CacheKeys.daily_breakdown("user1", date(2026, 6, 1))[0],
        CacheKeys.user_streak("user1")[0],
    }
    assert "user:user1:nutrition_bulk:*" in {
        operation.get("pattern") for operation in operations
    }


def test_backdated_meal_operations_include_current_week() -> None:
    operations = build_meal_invalidation_operations(
        "user1",
        date(2026, 5, 20),
        current_date=date(2026, 6, 3),
    )

    keys = {operation.get("key") for operation in operations}
    assert CacheKeys.weekly_budget("user1", date(2026, 5, 18))[0] in keys
    assert CacheKeys.weekly_budget("user1", date(2026, 6, 1))[0] in keys
