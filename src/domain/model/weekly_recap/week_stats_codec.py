"""Convert WeekStats to and from JSON-safe dicts.

Stored recaps keep this snapshot so a recap can be re-rendered in another
language with the same numbers. Decoding ignores unknown keys and leaves
missing optional fields at their defaults, so older snapshots keep loading
after new fields are added.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, fields, replace
from datetime import date
from typing import Any

from src.domain.model.weekly_recap.week_stats import (
    DishCount,
    LastWeek,
    MissedMeal,
    PeakMeal,
    WeekStats,
    Workout,
)

_NESTED: dict[str, type[Any]] = {
    "workouts": Workout,
    "top_dishes": DishCount,
    "weekend_peaks": PeakMeal,
}
_RECORDS = (Workout, DishCount, MissedMeal, PeakMeal, LastWeek)


def week_stats_to_dict(stats: WeekStats) -> dict[str, Any]:
    """Return ``stats`` as plain lists, numbers, strings and ``None``."""
    return {item.name: _plain(getattr(stats, item.name)) for item in fields(stats)}


def week_stats_from_dict(data: Mapping[str, Any]) -> WeekStats:
    """Rebuild WeekStats from :func:`week_stats_to_dict` output."""
    values = {
        item.name: _decode(item.name, data[item.name])
        for item in fields(WeekStats)
        if item.name in data
    }
    return WeekStats(**values)


def _plain(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, _RECORDS):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _decode(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name == "week_start":
        return date.fromisoformat(value)
    if name == "missed_meal":
        return _build(MissedMeal, value)
    if name == "last_week":
        last = _build(LastWeek, value)
        return replace(last, plan=tuple(last.plan))
    if name in _NESTED:
        return tuple(_build(_NESTED[name], item) for item in value)
    if isinstance(value, list):
        return tuple(value)
    return value


def _build(cls: type[Any], raw: Mapping[str, Any]) -> Any:
    known = {item.name for item in fields(cls)}
    return cls(**{key: item for key, item in raw.items() if key in known})
