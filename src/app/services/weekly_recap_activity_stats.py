"""Weekly recap numbers from workouts, drinks, weigh-ins, and the profile."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from src.domain.model.hydration.hydration_entry import HydrationEntry
from src.domain.model.movement.movement_entry import MovementEntry
from src.domain.model.weekly_recap import Workout
from src.domain.model.weight.weight_entry import WeightEntry
from src.domain.services.hydration_catalog_service import find_by_id
from src.domain.services.weekly_recap import goal_progress, monday_of
from src.domain.utils.timezone_utils import ensure_utc

STRENGTH_ACTIVITY_ID = "gym_strength"
GOALS = frozenset({"cut", "bulk", "recomp"})
DEFAULT_GOAL = "recomp"
_BACKDATED_AT = time(12, 0)


def recap_goal(fitness_goal: Any) -> str:
    """The profile's goal as the recap knows it: cut, bulk, or recomp."""
    value = str(getattr(fitness_goal, "value", fitness_goal) or "").strip().lower()
    return value if value in GOALS else DEFAULT_GOAL


def workouts(
    entries: Iterable[MovementEntry], week_start: date, tz: tzinfo
) -> tuple[Workout, ...]:
    """The week's workouts, oldest first."""
    return tuple(
        Workout(
            day=day,
            minutes=int(entry.duration_min),
            kcal=round(entry.kcal_burned or 0.0),
            strength=entry.activity_id == STRENGTH_ACTIVITY_ID,
        )
        for day, _, entry in _week_entries(entries, week_start, tz)
    )


def workout_starts(
    entries: Iterable[MovementEntry], week_start: date, tz: tzinfo
) -> list[tuple[int, datetime]]:
    """When each of the week's workouts began, as (day, local start).

    A workout is logged when it ends, so it began ``duration_min`` earlier.
    Workouts logged for a past day carry no real time and are left out.
    """
    return [
        (day, logged_at - timedelta(minutes=entry.duration_min))
        for day, logged_at, entry in _week_entries(entries, week_start, tz)
        if logged_at.time() != _BACKDATED_AT
    ]


def strength_record(
    entries: Iterable[MovementEntry], week_start: date, tz: tzinfo
) -> bool:
    """Whether the week had more strength sessions than any week before it.

    Needs an earlier week with a session to beat.
    """
    weeks: Counter[date] = Counter()
    for entry in entries:
        logged_at = ensure_utc(entry.logged_at)
        if logged_at is None or entry.duration_min <= 0:
            continue
        if entry.activity_id == STRENGTH_ACTIVITY_ID:
            weeks[monday_of(logged_at.astimezone(tz).date())] += 1
    best_before = max(
        (count for week, count in weeks.items() if week < week_start), default=0
    )
    return best_before > 0 and weeks[week_start] > best_before


def sugary_drinks(
    entries: Iterable[HydrationEntry], min_sugar_g: float
) -> tuple[int, int]:
    """Drinks with at least ``min_sugar_g`` of sugar, and their total kcal."""
    sugary = [entry for entry in entries if (entry.sugar_g or 0.0) >= min_sugar_g]
    return len(sugary), round(sum(entry.calories for entry in sugary))


def top_sugary_drink(
    entries: Iterable[HydrationEntry], min_sugar_g: float
) -> str | None:
    """The sugary drink behind more than half of the week's sugary drinks.

    Catalog drinks are counted by drink id and named by their catalog name,
    which the recap translates; custom drinks by their whitespace-tidied
    name. None without a clear favourite.
    """
    drinks: Counter[str] = Counter()
    for entry in entries:
        if (entry.sugar_g or 0.0) < min_sugar_g:
            continue
        catalog = find_by_id(entry.drink_id) if entry.drink_id else None
        name = catalog.name if catalog else " ".join(entry.drink_name_snapshot.split())
        if name:
            drinks[name] += 1
    if not drinks:
        return None
    name, count = drinks.most_common(1)[0]
    return name if count * 2 > drinks.total() else None


def weight_progress(
    goal: str,
    profile: Any,
    this_week: Sequence[WeightEntry],
    last_week: Sequence[WeightEntry],
) -> tuple[float | None, float | None]:
    """Progress to the target weight, from this week's average weigh-in, and
    its change since last week's average.

    None without a target weight, a weigh-in this week, or for recomp; the
    change is None without a weigh-in last week.
    """
    if profile is None:
        return None, None
    start = profile.goal_start_weight_kg or profile.weight_kg
    target = profile.target_weight_kg
    now = goal_progress(goal, start, _average_kg(this_week), target)
    before = goal_progress(goal, start, _average_kg(last_week), target)
    if now is None or before is None:
        return now, None
    return now, now - before


def _week_entries(
    entries: Iterable[MovementEntry], week_start: date, tz: tzinfo
) -> list[tuple[int, datetime, MovementEntry]]:
    out: list[tuple[int, datetime, MovementEntry]] = []
    for entry in entries:
        logged_at = ensure_utc(entry.logged_at)
        if logged_at is None or entry.duration_min <= 0:
            continue
        local = logged_at.astimezone(tz)
        day = (local.date() - week_start).days
        if 0 <= day < 7:
            out.append((day, local, entry))
    return sorted(out, key=lambda item: item[1])


def _average_kg(entries: Sequence[WeightEntry]) -> float | None:
    weights = [entry.weight_kg for entry in entries if entry.weight_kg > 0]
    return sum(weights) / len(weights) if weights else None
