"""Pure vacation window, streak freeze, and push-mute rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from src.domain.model.vacation.vacation import Vacation


@dataclass(frozen=True)
class VacationWindow:
    start: date
    end: date
    frozen_calories: float
    frozen_protein: float
    frozen_carbs: float
    frozen_fat: float


def to_window(vacation: Vacation) -> VacationWindow:
    return VacationWindow(
        start=vacation.start_date,
        end=vacation.effective_end,
        frozen_calories=vacation.frozen_calories,
        frozen_protein=vacation.frozen_protein,
        frozen_carbs=vacation.frozen_carbs,
        frozen_fat=vacation.frozen_fat,
    )


def covers(window: VacationWindow, day: date) -> bool:
    return window.start <= day <= window.end


def covering(windows: list[VacationWindow], day: date) -> VacationWindow | None:
    for window in windows:
        if covers(window, day):
            return window
    return None


def status_for(window: VacationWindow, today: date) -> str:
    if today < window.start:
        return "upcoming"
    if today > window.end:
        return "ended"
    return "active"


def open_vacation(vacations: list[Vacation], today: date) -> Vacation | None:
    """The upcoming or active vacation. Ended rows are history."""
    chosen: Vacation | None = None
    for vacation in vacations:
        window = to_window(vacation)
        state = status_for(window, today)
        if state == "active":
            return vacation
        if state == "upcoming" and chosen is None:
            chosen = vacation
    return chosen


def _vacation_days(windows: list[VacationWindow]) -> set[date]:
    days: set[date] = set()
    for window in windows:
        cursor = window.start
        while cursor <= window.end:
            days.add(cursor)
            cursor += timedelta(days=1)
    return days


def current_streak(
    logged: set[date], today: date, windows: list[VacationWindow]
) -> int:
    """Count logged days, skipping vacation days without adding or breaking."""
    vacation_days = _vacation_days(windows)
    countable = logged - vacation_days
    cursor = today
    if today not in vacation_days and today not in countable:
        cursor = today - timedelta(days=1)

    streak = 0
    for _ in range(366 * 5):
        if cursor in vacation_days:
            cursor -= timedelta(days=1)
            continue
        if cursor in countable:
            streak += 1
            cursor -= timedelta(days=1)
            continue
        break
    return streak


def best_streak(logged: set[date], windows: list[VacationWindow]) -> int:
    """Longest run. A gap that is only vacation days does not split the run."""
    vacation_days = _vacation_days(windows)
    ordered = sorted(logged - vacation_days)
    if not ordered:
        return 0
    best = 1
    run = 1
    for previous, current in zip(ordered, ordered[1:], strict=False):
        gap = [
            previous + timedelta(days=offset)
            for offset in range(1, (current - previous).days)
        ]
        if all(day in vacation_days for day in gap):
            run += 1
        else:
            best = max(best, run)
            run = 1
    return max(best, run)


def should_mute_push(kind: str, on_vacation: bool) -> bool:
    if not on_vacation:
        return False
    return kind != "vacation_ended"


def quiet_day_hold(
    windows: list[VacationWindow], day: date, had_meals: bool
) -> VacationWindow | None:
    """Frozen macros to impute when a past vacation day had no meals."""
    if had_meals:
        return None
    return covering(windows, day)
