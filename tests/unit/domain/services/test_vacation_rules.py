"""Vacation freeze rules: streak, mute, and quiet-day imputation."""

from datetime import date, timedelta

from src.domain.services.vacation_rules import (
    VacationWindow,
    best_streak,
    current_streak,
    quiet_day_hold,
    should_mute_push,
)


def _window(start: date, end: date) -> VacationWindow:
    return VacationWindow(
        start=start,
        end=end,
        frozen_calories=1860,
        frozen_protein=120,
        frozen_carbs=180,
        frozen_fat=60,
    )


def test_active_vacation_holds_streak_and_ignores_trip_logs():
    start = date(2026, 10, 12)
    logged = {start - timedelta(days=offset) for offset in range(1, 4)}
    logged.add(date(2026, 10, 14))
    windows = [_window(start, date(2026, 10, 18))]

    assert current_streak(logged, date(2026, 10, 15), windows) == 3


def test_return_continues_from_the_frozen_streak():
    start = date(2026, 10, 12)
    logged = {start - timedelta(days=offset) for offset in range(1, 4)}
    logged.add(date(2026, 10, 19))
    windows = [_window(start, date(2026, 10, 18))]

    assert current_streak(logged, date(2026, 10, 20), windows) == 4


def test_missing_the_day_after_vacation_breaks_the_streak():
    start = date(2026, 10, 12)
    logged = {start - timedelta(days=1)}
    windows = [_window(start, date(2026, 10, 18))]

    assert current_streak(logged, date(2026, 10, 20), windows) == 0


def test_best_streak_bridges_vacation_without_counting_those_days():
    logged = {
        date(2026, 10, 10),
        date(2026, 10, 11),
        date(2026, 10, 19),
        date(2026, 10, 20),
    }
    windows = [_window(date(2026, 10, 12), date(2026, 10, 18))]

    assert best_streak(logged, windows) == 4


def test_mute_keeps_only_the_return_note():
    assert should_mute_push("meal_reminder_lunch", True) is True
    assert should_mute_push("vacation_ended", True) is False
    assert should_mute_push("meal_reminder_lunch", False) is False


def test_quiet_vacation_day_is_imputed_and_a_logged_day_is_not():
    window = _window(date(2026, 10, 12), date(2026, 10, 18))
    assert quiet_day_hold([window], date(2026, 10, 13), had_meals=False) == window
    assert quiet_day_hold([window], date(2026, 10, 13), had_meals=True) is None
