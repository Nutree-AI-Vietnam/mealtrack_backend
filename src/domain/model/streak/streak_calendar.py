"""The logging streak on a user's calendar, with restored days.

A logged day counts toward the streak. A restored day fills a missed day: it
keeps the streak unbroken but adds nothing to the count. From
``same_day_from`` on, a day is logged only when something was logged on the
day itself; a log added later for a past day still shows in the diary, but it
neither counts nor bridges.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

# A restore is offered only when the run that broke was at least this long.
MIN_BROKEN_STREAK = 3
# Days after the first missed day during which it can still be restored.
RESTORE_WINDOW_DAYS = 2
# Restored days the app draws (any Monday-Sunday week it shows).
RECENT_RESTORED_DAYS = 14

_DAY = timedelta(days=1)


@dataclass(frozen=True)
class RestoreOffer:
    """The latest missed days that can still be restored."""

    missed_dates: tuple[date, ...]
    restorable_until: date
    broken_streak: int


@dataclass(frozen=True)
class StreakCalendar:
    current_streak: int
    best_streak: int
    last_logged_date: date | None
    offer: RestoreOffer | None
    restored_recent: tuple[date, ...]


def build_streak_calendar(
    log_days: Iterable[tuple[date, bool]],
    restored: Iterable[date],
    today: date,
    same_day_from: date | None,
) -> StreakCalendar:
    """Streak numbers and restore offer from the days with logs.

    ``log_days`` holds each local day with any log and whether one of them was
    made on the day itself.
    """
    logged = {
        day
        for day, same_day in log_days
        if same_day or same_day_from is None or day < same_day_from
    }
    restored_only = set(restored) - logged
    kept = logged | restored_only
    return StreakCalendar(
        current_streak=_current_streak(logged, kept, today),
        best_streak=_best_streak(logged, kept),
        last_logged_date=max(logged) if logged else None,
        offer=_offer(logged, kept, today),
        restored_recent=tuple(
            sorted(
                day
                for day in restored_only
                if day >= today - timedelta(days=RECENT_RESTORED_DAYS - 1)
            )
        ),
    )


def _logged_in_run(logged: set[date], kept: set[date], last: date) -> int:
    """Logged days in the unbroken run of kept days that ends on ``last``."""
    count = 0
    cursor = last
    while cursor in kept:
        count += cursor in logged
        cursor -= _DAY
    return count


def _current_streak(logged: set[date], kept: set[date], today: date) -> int:
    # The streak is not broken until today ends.
    return _logged_in_run(logged, kept, today if today in logged else today - _DAY)


def _best_streak(logged: set[date], kept: set[date]) -> int:
    best = 0
    for start in kept:
        if start - _DAY in kept:
            continue
        count = 0
        cursor = start
        while cursor in kept:
            count += cursor in logged
            cursor += _DAY
        best = max(best, count)
    return best


def _offer(logged: set[date], kept: set[date], today: date) -> RestoreOffer | None:
    """The latest run of missed days ending yesterday or the day before.

    Logs after the run do not close it; only the window does.
    """
    end = next(
        (day for day in (today - _DAY, today - 2 * _DAY) if day not in kept), None
    )
    if end is None:
        return None
    first = end
    while first - _DAY not in kept and _restorable(first, today):
        first -= _DAY
    if not _restorable(first, today):
        return None
    broken = _logged_in_run(logged, kept, first - _DAY)
    if broken < MIN_BROKEN_STREAK:
        return None
    return RestoreOffer(
        missed_dates=tuple(
            first + timedelta(days=offset) for offset in range((end - first).days + 1)
        ),
        restorable_until=first + timedelta(days=RESTORE_WINDOW_DAYS),
        broken_streak=broken,
    )


def _restorable(first_missed: date, today: date) -> bool:
    return first_missed + timedelta(days=RESTORE_WINDOW_DAYS) >= today
