from datetime import UTC, datetime

import pytest

from src.domain.model.movement import MovementEntry
from src.domain.model.movement.movement_load import (
    daily_movement_load,
    intensity_factor,
    movement_kind,
)


def _entry(
    minutes: int,
    intensity: str = "moderate",
    activity_id: str | None = "running",
    name: str = "Running",
    included: bool = True,
) -> MovementEntry:
    return MovementEntry(
        user_id="u1",
        activity_name=name,
        duration_min=minutes,
        kcal_burned=100.0,
        intensity=intensity,
        logged_at=datetime(2026, 10, 30, 7, 0, tzinfo=UTC),
        activity_id=activity_id,
        include_in_balance=included,
    )


def test_load_is_minutes_times_intensity():
    load = daily_movement_load(
        [
            _entry(30, "light", "walking", "Walking"),
            _entry(20, "moderate"),
            _entry(10, "hard", "gym_strength", "Gym"),
        ]
    )
    assert load.minutes == 60
    assert load.sessions == 3
    assert load.load == 30 * 1 + 20 * 2 + 10 * 3
    assert load.kinds == ["walk", "run", "gym"]


def test_sessions_kept_out_of_the_balance_still_count_as_load():
    load = daily_movement_load([_entry(40, "moderate", included=False)])
    assert load.sessions == 1
    assert load.load == 80.0


def test_empty_day():
    load = daily_movement_load([])
    assert (load.minutes, load.sessions, load.load, load.kinds) == (0, 0, 0.0, [])


def test_unknown_intensity_counts_as_moderate():
    assert intensity_factor("extreme") == 2.0
    assert intensity_factor(None) == 2.0
    assert intensity_factor(" HARD ") == 3.0


@pytest.mark.parametrize(
    ("activity_id", "name", "kind"),
    [
        ("swimming", "Swimming", "swim"),
        ("cycling", "Cycling", "cycle"),
        ("cardio_hiit", "HIIT", "gym"),
        ("badminton", "Cầu lông", "other"),
        (None, "Chạy bộ buổi sáng", "run"),
        (None, "Đạp xe", "cycle"),
        (None, "Bơi lội", "swim"),
        (None, "Evening walk", "walk"),
        ("custom", "Weight training", "gym"),
        (None, "Dancing", "other"),
    ],
)
def test_kind_from_catalog_id_then_name(activity_id, name, kind):
    assert movement_kind(activity_id, name) == kind
