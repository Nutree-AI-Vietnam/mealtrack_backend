"""Daily training load for the Trends Tập luyện card.

Load is minutes × an intensity factor (light 1, moderate 2, hard 3). Every
logged session counts, including ones kept out of the calorie balance, since
they still took effort. Kinds pick the art in the card's 7-day strip.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

_INTENSITY_FACTORS: Final[dict[str, float]] = {
    "light": 1.0,
    "moderate": 2.0,
    "hard": 3.0,
}
_DEFAULT_FACTOR: Final = 2.0

_KIND_BY_ACTIVITY_ID: Final[dict[str, str]] = {
    "running": "run",
    "walking": "walk",
    "gym_strength": "gym",
    "cardio_hiit": "gym",
    "swimming": "swim",
    "cycling": "cycle",
}
# For sessions without a catalog id (custom names, Health imports), English
# and Vietnamese words in the name, checked in this order.
_KIND_BY_NAME_WORD: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("run", ("run", "jog", "chạy")),
    ("walk", ("walk", "hike", "đi bộ", "leo núi")),
    ("swim", ("swim", "bơi")),
    ("cycle", ("cycl", "bike", "biking", "đạp xe")),
    ("gym", ("strength", "gym", "weight", "hiit", "tạ")),
)
KINDS: Final = ("run", "walk", "gym", "swim", "cycle", "other")


@dataclass(frozen=True)
class DailyMovementLoad:
    minutes: int
    sessions: int
    load: float
    kinds: list[str]


def intensity_factor(intensity: str | None) -> float:
    return _INTENSITY_FACTORS.get((intensity or "").strip().lower(), _DEFAULT_FACTOR)


def movement_kind(activity_id: str | None, activity_name: str | None) -> str:
    kind = _KIND_BY_ACTIVITY_ID.get(activity_id or "")
    if kind:
        return kind
    name = (activity_name or "").casefold()
    for candidate, words in _KIND_BY_NAME_WORD:
        if any(word in name for word in words):
            return candidate
    return "other"


def daily_movement_load(entries: Iterable[Any]) -> DailyMovementLoad:
    """Roll up one day's movement entries; kinds keep first-seen order."""
    minutes = 0
    sessions = 0
    load = 0.0
    kinds: list[str] = []
    for entry in entries:
        duration = max(int(getattr(entry, "duration_min", 0) or 0), 0)
        minutes += duration
        sessions += 1
        load += duration * intensity_factor(getattr(entry, "intensity", None))
        kind = movement_kind(
            getattr(entry, "activity_id", None), getattr(entry, "activity_name", None)
        )
        if kind not in kinds:
            kinds.append(kind)
    return DailyMovementLoad(
        minutes=minutes, sessions=sessions, load=round(load, 1), kinds=kinds
    )
