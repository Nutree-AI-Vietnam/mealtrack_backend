"""Week math behind WeekStats: weeks, streaks, calorie days and goals."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from src.domain.services.weekly_recap import (
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

MONDAY = date(2023, 10, 2)
TARGETS = [1900.0] * 7


def _days(first: date, count: int) -> list[date]:
    return [first + timedelta(days=offset) for offset in range(count)]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2023, 10, 2), MONDAY),
        (date(2023, 10, 4), MONDAY),
        (date(2023, 10, 8), MONDAY),
        (date(2023, 10, 9), date(2023, 10, 9)),
    ],
)
def test_monday_of(day: date, expected: date) -> None:
    assert monday_of(day) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2023, 10, 9, 7, 59), date(2023, 9, 25)),
        (datetime(2023, 10, 9, 8, 0), MONDAY),
        (datetime(2023, 10, 11, 1, 0), MONDAY),
        (datetime(2023, 10, 15, 23, 59), MONDAY),
    ],
)
def test_latest_week_is_out_on_monday_at_the_publish_hour(
    now: datetime, expected: date
) -> None:
    assert latest_week_start(now, publish_hour=8) == expected


def test_week_number_counts_from_the_sign_up_week() -> None:
    assert week_number(MONDAY, created=date(2023, 10, 4)) == 1
    assert week_number(MONDAY, created=date(2023, 10, 1)) == 2
    assert week_number(MONDAY, created=date(2023, 7, 3)) == 14


def test_account_days_include_the_sign_up_day() -> None:
    assert account_days(MONDAY, created=MONDAY) == 7
    assert account_days(MONDAY, created=date(2023, 10, 4)) == 5
    assert account_days(MONDAY, created=date(2023, 9, 1)) == 38


@pytest.mark.parametrize(
    ("created", "expected"),
    [
        (date(2023, 9, 1), 7),
        (MONDAY, 7),
        (date(2023, 10, 4), 5),
        (date(2023, 10, 8), 1),
        (date(2023, 10, 9), 0),
    ],
)
def test_week_days_count_only_days_the_account_existed(
    created: date, expected: int
) -> None:
    assert week_days(MONDAY, created) == expected


def test_streak_ends_on_sunday_and_beats_an_earlier_run() -> None:
    logs = [*_days(date(2023, 9, 20), 3), *_days(MONDAY, 7)]

    assert streak_stats(logs, MONDAY, created=date(2023, 9, 1)) == (7, 9, True)


def test_streak_is_zero_without_a_sunday_log() -> None:
    logs = _days(MONDAY, 6)

    assert streak_stats(logs, MONDAY, created=MONDAY) == (0, 0, False)


def test_first_run_is_not_a_record() -> None:
    created = date(2023, 9, 30)
    logs = _days(date(2023, 10, 4), 5)

    assert streak_stats(logs, MONDAY, created) == (5, 4, False)


def test_streak_continues_from_last_week_and_ignores_later_logs() -> None:
    logs = [date(2023, 10, 1), *_days(MONDAY, 7), date(2023, 10, 9)]

    assert streak_stats(logs, MONDAY, created=date(2023, 9, 1)) == (8, 0, False)


def test_no_logs_in_the_week_means_no_gap() -> None:
    logs = [date(2023, 9, 20)]

    assert streak_stats(logs, MONDAY, created=date(2023, 9, 1)) == (0, 0, False)


def test_weekend_delta_averages_counted_friday_to_sunday() -> None:
    kcal = [0.0, 0.0, 0.0, 0.0, 2400.0, 2500.0, 2300.0]

    assert weekend_delta(kcal, TARGETS, counted=[4, 5, 6]) == 500.0
    assert weekend_delta(kcal, TARGETS, counted=[0, 1, 4, 5]) == 550.0
    assert weekend_delta(kcal, TARGETS, counted=[5]) == 0.0


def test_under_days_are_counted_days_below_the_ratio() -> None:
    kcal = [1500.0, 1800.0, 1000.0, 1900.0, 0.0, 0.0, 0.0]

    assert under_days(kcal, TARGETS, counted=[0, 1, 3], ratio=0.9) == (0,)


def test_training_day_kcal_averages_counted_workout_days() -> None:
    kcal = [2000.0, 2600.0, 2800.0, 0.0, 0.0, 3000.0, 0.0]

    assert training_day_kcal(kcal, counted=[0, 1, 2], workout_days=[1, 2, 5]) == 2700
    assert training_day_kcal(kcal, counted=[0], workout_days=[1]) == 0.0


@pytest.mark.parametrize(
    ("hour", "meal"),
    [
        (4, "snack"),
        (5, "breakfast"),
        (10, "breakfast"),
        (11, "lunch"),
        (14, "lunch"),
        (15, "snack"),
        (17, "dinner"),
        (21, "dinner"),
        (22, "snack"),
    ],
)
def test_meal_for_hour(hour: int, meal: str) -> None:
    assert meal_for_hour(hour) == meal


@pytest.mark.parametrize(
    ("goal", "start", "current", "target", "expected"),
    [
        ("cut", 80.0, 75.0, 70.0, 0.5),
        ("cut", 80.0, 68.0, 70.0, 1.0),
        ("cut", 80.0, 82.0, 70.0, 0.0),
        ("bulk", 60.0, 63.0, 70.0, 0.3),
        ("cut", 70.0, 70.0, 75.0, None),
        ("recomp", 70.0, 70.0, 70.0, None),
        ("cut", None, 75.0, 70.0, None),
        ("cut", 80.0, 75.0, None, None),
    ],
)
def test_goal_progress(
    goal: str,
    start: float | None,
    current: float | None,
    target: float | None,
    expected: float | None,
) -> None:
    progress = goal_progress(goal, start, current, target)

    if expected is None:
        assert progress is None
    else:
        assert progress == pytest.approx(expected)
