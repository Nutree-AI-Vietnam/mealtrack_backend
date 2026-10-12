"""Pure helpers that turn a user's logs into WeekStats numbers.

Weeks run Monday to Sunday in the user's timezone. Day indexes are 0
(Monday) .. 6 (Sunday); ``counted`` days are logged days that are not
cheat days.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta

_WEEKEND = (4, 5, 6)
_MIN_WEEKEND_DAYS = 2


def monday_of(day: date) -> date:
    """Monday of the week that contains ``day``."""
    return day - timedelta(days=day.weekday())


def latest_week_start(now_local: datetime, publish_hour: int) -> date:
    """Monday of the newest week whose recap is out by ``now_local``.

    A week's recap comes out the following Monday at ``publish_hour``;
    until then the week before it is still the latest.
    """
    monday = monday_of(now_local.date())
    if now_local.weekday() > 0 or now_local.hour >= publish_hour:
        return monday - timedelta(days=7)
    return monday - timedelta(days=14)


def week_number(week_start: date, created: date) -> int:
    """1 for the week the account was created in, 2 for the next, and so on."""
    return (week_start - monday_of(created)).days // 7 + 1


def account_days(week_start: date, created: date) -> int:
    """Days the account existed by the end of the week, sign-up day included."""
    return (week_start + timedelta(days=7) - created).days


def week_days(week_start: date, created: date) -> int:
    """Days of the week the account existed for: 7 unless created mid-week."""
    end = week_start + timedelta(days=7)
    if created >= end:
        return 0
    return min(7, (end - created).days)


def streak_stats(
    log_dates: Iterable[date], week_start: date, created: date
) -> tuple[int, int, bool]:
    """The streak at the end of the week, the gap before it, and whether it
    is a record.

    The streak counts consecutive logged days ending on Sunday, so it is 0
    when Sunday was not logged. The gap counts the unlogged days right
    before the week's first logged day, back to the previous log or, for a
    user's first logs, to sign-up. A record needs an earlier run to beat.
    """
    week_end = week_start + timedelta(days=6)
    days = sorted({day for day in log_dates if day <= week_end})
    runs = _runs(days)
    streak = runs[-1][1] if runs and runs[-1][0] == week_end else 0
    earlier = [length for _, length in runs[:-1]]
    is_record = streak > 0 and bool(earlier) and streak > max(earlier)
    return streak, _gap_before(days, week_start, created), is_record


def weekend_delta(
    kcal: Sequence[float], targets: Sequence[float], counted: Iterable[int]
) -> float:
    """Average kcal above target on counted Friday-to-Sunday days.

    0 when fewer than two of those days count.
    """
    included = set(counted)
    days = [day for day in _WEEKEND if day in included]
    if len(days) < _MIN_WEEKEND_DAYS:
        return 0.0
    return sum(kcal[day] - targets[day] for day in days) / len(days)


def under_days(
    kcal: Sequence[float],
    targets: Sequence[float],
    counted: Iterable[int],
    ratio: float,
) -> tuple[int, ...]:
    """Counted days that came in below ``ratio`` of the day's target."""
    return tuple(
        day for day in sorted(set(counted)) if kcal[day] < targets[day] * ratio
    )


def training_day_kcal(
    kcal: Sequence[float], counted: Iterable[int], workout_days: Iterable[int]
) -> float:
    """Average kcal eaten on counted days with a workout; 0 without any."""
    included = set(counted)
    days = [day for day in sorted(set(workout_days)) if day in included]
    return sum(kcal[day] for day in days) / len(days) if days else 0.0


def meal_for_hour(hour: int) -> str:
    """The meal people usually log at ``hour`` (local time)."""
    if 5 <= hour <= 10:
        return "breakfast"
    if 11 <= hour <= 14:
        return "lunch"
    if 17 <= hour <= 21:
        return "dinner"
    return "snack"


def goal_progress(
    goal: str, start: float | None, current: float | None, target: float | None
) -> float | None:
    """How far from the start weight to the target weight, from 0 to 1.

    None when a weight is missing, when the target lies the wrong way for
    the goal, or for a goal without a target weight (recomp).
    """
    if start is None or current is None or target is None:
        return None
    if goal == "cut":
        span, done = start - target, start - current
    elif goal == "bulk":
        span, done = target - start, current - start
    else:
        return None
    if span <= 0:
        return None
    return min(1.0, max(0.0, done / span))


def _runs(days: Sequence[date]) -> list[tuple[date, int]]:
    """Consecutive-day runs in sorted ``days``, as (last day, length)."""
    runs: list[tuple[date, int]] = []
    for day in days:
        if runs and day - runs[-1][0] == timedelta(days=1):
            runs[-1] = (day, runs[-1][1] + 1)
        else:
            runs.append((day, 1))
    return runs


def _gap_before(days: Sequence[date], week_start: date, created: date) -> int:
    first = next((day for day in days if day >= week_start), None)
    if first is None:
        return 0
    previous = [day for day in days if day < week_start]
    if previous:
        return (first - previous[-1]).days - 1
    return max(0, (first - created).days)
