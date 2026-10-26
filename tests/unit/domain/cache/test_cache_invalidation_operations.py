from datetime import date, timedelta

import pytest

from src.domain.cache.cache_invalidation_operations import (
    BUMP_REVISION,
    DELETE_KEY,
    DELETE_PATTERN,
    build_hydration_invalidation_operations,
    build_meal_invalidation_operations,
    build_movement_invalidation_operations,
)
from src.domain.cache.cache_keys import CacheKeys


def test_meal_operations_preserve_current_week_cache_coverage() -> None:
    operations = build_meal_invalidation_operations(
        "user1",
        date(2026, 6, 2),
        current_date=date(2026, 6, 3),
    )

    # The write path never SCANs: exact keys plus one revision bump.
    assert {operation["op"] for operation in operations} == {
        DELETE_KEY,
        BUMP_REVISION,
    }
    assert {operation.get("key") for operation in operations} >= {
        CacheKeys.daily_macros("user1", date(2026, 6, 2))[0],
        CacheKeys.weekly_budget("user1", date(2026, 6, 1))[0],
        CacheKeys.daily_breakdown("user1", date(2026, 6, 1))[0],
        CacheKeys.user_streak("user1")[0],
    }
    bumps = [op for op in operations if op["op"] == BUMP_REVISION]
    assert bumps == [
        {
            "op": BUMP_REVISION,
            "key": CacheKeys.intake_revision("user1")[0],
            "ttl": str(CacheKeys.intake_revision("user1")[1]),
        }
    ]


@pytest.mark.parametrize(
    "build",
    [
        build_meal_invalidation_operations,
        build_hydration_invalidation_operations,
        build_movement_invalidation_operations,
    ],
)
def test_write_operations_name_every_weekly_budget_target_date(build) -> None:
    """Target-date budget keys used to need a SCAN; a week has only seven."""
    operations = build("user1", date(2026, 6, 2), current_date=date(2026, 6, 3))

    keys = {operation.get("key") for operation in operations}
    week_start = date(2026, 6, 1)
    for offset in range(7):
        target = week_start + timedelta(days=offset)
        assert CacheKeys.weekly_budget("user1", week_start, target)[0] in keys
    assert all(operation["op"] != DELETE_PATTERN for operation in operations)


def test_backdated_meal_operations_include_current_week() -> None:
    operations = build_meal_invalidation_operations(
        "user1",
        date(2026, 5, 20),
        current_date=date(2026, 6, 3),
    )

    keys = {operation.get("key") for operation in operations}
    assert CacheKeys.weekly_budget("user1", date(2026, 5, 18))[0] in keys
    assert CacheKeys.weekly_budget("user1", date(2026, 6, 1))[0] in keys
