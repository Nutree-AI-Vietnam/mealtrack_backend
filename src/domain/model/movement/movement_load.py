"""Daily training load for the Trends Tập luyện card.

Load is minutes × an intensity factor (light 1, moderate 2, hard 3). Every
logged session counts, including ones kept out of the calorie balance, since
they still took effort. Kinds pick the art in the card's 7-day strip.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from src.domain.services.movement_catalog_service import (
    activity_id_for_name,
    get_activity,
)

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
# For sessions without a catalog id or name (custom names, Health imports),
# words in the name, checked in this order. English stems match at the start
# of a word ("running"); Vietnamese words match whole, so "tạ" skips "tại"
# and "run" skips "trung".
_ENGLISH_STEMS: Final[dict[str, tuple[str, ...]]] = {
    "run": ("run", "jog"),
    "walk": ("walk", "hike", "hiking"),
    "swim": ("swim",),
    "cycle": ("cycl", "bike", "biking"),
    "gym": ("strength", "gym", "weight", "hiit"),
}
_VIETNAMESE_WORDS: Final[dict[str, tuple[str, ...]]] = {
    "run": ("chạy",),
    "walk": ("đi bộ", "leo núi"),
    "swim": ("bơi",),
    "cycle": ("đạp xe",),
    "gym": ("tạ",),
}
_KIND_BY_NAME_PATTERN: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (
        kind,
        re.compile(
            "|".join(
                [rf"\b{re.escape(stem)}" for stem in _ENGLISH_STEMS[kind]]
                + [rf"\b{re.escape(word)}\b" for word in _VIETNAMESE_WORDS[kind]]
            )
        ),
    )
    for kind in ("run", "walk", "swim", "cycle", "gym")
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
    catalog_id = activity_id if get_activity(activity_id) else None
    catalog_id = catalog_id or activity_id_for_name(activity_name)
    if catalog_id:
        return _KIND_BY_ACTIVITY_ID.get(catalog_id, "other")
    name = unicodedata.normalize("NFC", activity_name or "").casefold()
    for kind, pattern in _KIND_BY_NAME_PATTERN:
        if pattern.search(name):
            return kind
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
