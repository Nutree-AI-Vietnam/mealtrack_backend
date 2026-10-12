"""What every insight sees: the week, its derived facts, config and locale."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.domain.model.weekly_recap import WeekStats
from src.domain.services.weekly_recap.config import RecapConfig
from src.domain.services.weekly_recap.facts import WeekFacts
from src.domain.services.weekly_recap.format import (
    day_name,
    days_span,
    format_dec1,
    format_hour,
    format_hour_range,
    format_int,
    format_time,
    join_days,
    join_list,
    js_round,
)
from src.domain.services.weekly_recap.i18n import text

_MEALS = frozenset({"breakfast", "lunch", "dinner", "snack"})


@dataclass(frozen=True)
class RecapContext:
    """One week rendered in one locale, with shortcuts for its copy."""

    stats: WeekStats
    facts: WeekFacts
    config: RecapConfig
    locale: str

    def t(self, key: str, count: int | None = None, **params: object) -> str:
        """Localized copy; ``count`` picks the singular form."""
        return text(self.locale, key, count, **params)

    def num(self, value: float) -> str:
        """Rounded whole number with the locale's thousands separator."""
        return format_int(js_round(value), self.locale)

    def dec(self, value: float) -> str:
        """Number with one decimal place."""
        return format_dec1(value, self.locale)

    def day(self, index: int) -> str:
        """Short weekday name; 0 is Monday."""
        return day_name(index, self.locale)

    def days(self, indexes: Sequence[int]) -> str:
        """Several weekdays, joined."""
        return join_days(indexes, self.locale)

    def span(self, indexes: Sequence[int]) -> str:
        """Weekdays, written as a range when three or more are consecutive."""
        return days_span(indexes, self.locale)

    def names(self, items: Sequence[str]) -> str:
        """Free text items joined into one list."""
        return join_list(items, self.locale)

    def hour(self, hour: int) -> str:
        """Clock hour."""
        return format_hour(hour, self.locale)

    def time(self, minutes: int) -> str:
        """Clock time, from minutes after midnight."""
        return format_time(minutes, self.locale)

    def hour_range(self, hour: int) -> str:
        """The hour-long window starting at ``hour``."""
        return format_hour_range(hour, self.locale)

    def meal(self, meal_type: str | None) -> str:
        """Localized meal name, or "" for a missing or unknown type."""
        if meal_type is None or meal_type not in _MEALS:
            return ""
        return self.t(f"meal.{meal_type}")
