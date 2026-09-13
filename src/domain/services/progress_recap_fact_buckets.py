"""Window helpers for recap facts: best day, swing, weekend, month."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import mean
from typing import Any


def best_day(logged: list[dict[str, Any]]) -> tuple[str | None, float | None]:
    best_key: str | None = None
    best_abs = float("inf")
    best_balance: float | None = None
    for row in logged:
        target = _num(row, "target_calories")
        if target <= 0:
            continue
        balance = _num(row, "calories") - target
        if abs(balance) < best_abs:
            best_abs = abs(balance)
            best_key = str(row.get("date") or "") or None
            best_balance = round(balance, 1)
    return best_key, best_balance


def calorie_cv(values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    avg = mean(values)
    if avg <= 0:
        return None
    return round(mean(abs(value - avg) for value in values) / avg, 3)


def weekend_gap(logged: list[dict[str, Any]]) -> float | None:
    weekday: list[float] = []
    weekend: list[float] = []
    for row in logged:
        day = parse_date(row.get("date"))
        if day is None:
            continue
        bucket = weekend if day.weekday() >= 5 else weekday
        bucket.append(_num(row, "calories"))
    if not weekday or not weekend:
        return None
    return round(mean(weekend) - mean(weekday), 1)


def best_bucket(logged: list[dict[str, Any]], key_fn: Any) -> str | None:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in logged:
        day = parse_date(row.get("date"))
        target = _num(row, "target_calories")
        if day is None or target <= 0:
            continue
        buckets[key_fn(day)].append(abs(_num(row, "calories") / target - 1))
    if not buckets:
        return None
    return min(buckets, key=lambda key: mean(buckets[key]))


def iso_week(value: date) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def year_month(value: date) -> str:
    return f"{value.year}-{value.month:02d}"


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _num(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0
