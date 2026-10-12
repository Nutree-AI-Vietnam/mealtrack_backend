"""Locale formatting for weekly recap copy: numbers, weekdays, hours, ranges.

Rounding follows the design engine (JavaScript ``Math.round`` and
``toFixed(1)``), so the backend renders the same figures as the mockups.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

_GROUP = {"vi": ".", "de": ".", "es": ".", "fr": " "}
_DECIMAL = {"vi": ",", "de": ",", "es": ",", "fr": ","}
_DAYS = {
    "vi": ("T2", "T3", "T4", "T5", "T6", "T7", "CN"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    "ja": ("月", "火", "水", "木", "金", "土", "日"),
    "zh": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
}
_EN_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_DAY_JOIN = {"ja": "・", "zh": "、"}
_LIST_JOIN = {"ja": "、", "zh": "、"}
_SPAN_MIN_DAYS = 3


def js_round(value: float) -> int:
    """Round half up like JavaScript ``Math.round``."""
    return math.floor(value + 0.5)


def format_int(value: int, locale: str) -> str:
    """Group thousands with the locale separator: 1.900, 1,900, 1 900."""
    digits = f"{abs(value):,}".replace(",", _GROUP.get(locale, ","))
    return f"-{digits}" if value < 0 else digits


def format_dec1(value: float, locale: str) -> str:
    """One decimal, rounded like JavaScript ``toFixed(1)``: 2,5 or 2.5."""
    rounded = Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return str(rounded).replace(".", _DECIMAL.get(locale, "."))


def day_name(index: int, locale: str) -> str:
    """Short weekday name; 0 is Monday."""
    return _DAYS.get(locale, _DAYS["en"])[index]


def join_days(indexes: Sequence[int], locale: str) -> str:
    """Join weekdays: "Sat & Sun", "Mon, Wed & Fri", "土・日"."""
    names = [day_name(index, locale) for index in indexes]
    if locale in _DAY_JOIN:
        return _DAY_JOIN[locale].join(names)
    if len(names) <= 2:
        return " & ".join(names)
    return ", ".join(names[:-1]) + " & " + names[-1]


def days_span(indexes: Sequence[int], locale: str) -> str:
    """Three or more consecutive days read as a span: "Fri – Sun"."""
    days = sorted(set(indexes))
    consecutive = bool(days) and days[-1] - days[0] == len(days) - 1
    if consecutive and len(days) >= _SPAN_MIN_DAYS:
        return f"{day_name(days[0], locale)} – {day_name(days[-1], locale)}"
    return join_days(days, locale)


def join_list(items: Sequence[str], locale: str) -> str:
    """Join free text such as dish names or plan items."""
    return _LIST_JOIN.get(locale, ", ").join(items)


def format_range(start: date, locale: str) -> str:
    """Monday-to-Sunday range: "2/10 – 8/10", "Oct 2 – Oct 8"."""
    end = start + timedelta(days=6)
    return f"{_short_date(start, locale)} – {_short_date(end, locale)}"


def format_hour(hour: int, locale: str) -> str:
    """Clock hour: "22h", "10 PM", "22:00", "22 Uhr", "22時", "22点"."""
    if locale == "en":
        return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"
    if locale == "de":
        return f"{hour} Uhr"
    return _hour_token(hour, locale)


def format_hour_range(hour: int, locale: str) -> str:
    """One-hour window starting at ``hour``: "12h–13h", "12–13 Uhr"."""
    end = (hour + 1) % 24
    if locale == "de":
        return f"{hour}–{end} Uhr"
    return f"{format_hour(hour, locale)}–{format_hour(end, locale)}"


def format_time(minutes: int, locale: str) -> str:
    """Clock time from minutes after midnight: "8:30 AM", "8h30", "8時30分".

    Whole hours read like ``format_hour``.
    """
    hour, minute = divmod(minutes % 1440, 60)
    if minute == 0:
        return format_hour(hour, locale)
    if locale == "en":
        return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"
    if locale == "de":
        return f"{hour}:{minute:02d} Uhr"
    if locale == "es":
        return f"{hour}:{minute:02d}"
    if locale == "ja":
        return f"{hour}時{minute}分"
    if locale == "zh":
        return f"{hour}点{minute}分"
    return f"{hour}h{minute:02d}"


def _hour_token(hour: int, locale: str) -> str:
    if locale == "es":
        return f"{hour}:00"
    if locale == "ja":
        return f"{hour}時"
    if locale == "zh":
        return f"{hour}点"
    return f"{hour}h"


def _short_date(day: date, locale: str) -> str:
    if locale == "en":
        return f"{_EN_MONTHS[day.month - 1]} {day.day}"
    if locale == "de":
        return f"{day.day}.{day.month}."
    if locale in ("ja", "zh"):
        return f"{day.month}/{day.day}"
    return f"{day.day}/{day.month}"
