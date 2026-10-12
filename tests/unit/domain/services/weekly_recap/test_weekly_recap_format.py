"""Recap number, weekday, hour and date formatting per locale."""

from __future__ import annotations

from datetime import date

import pytest

from src.domain.services.weekly_recap.format import (
    day_name,
    days_span,
    format_dec1,
    format_hour,
    format_hour_range,
    format_int,
    format_range,
    format_time,
    join_days,
    join_list,
    js_round,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, 1), (1.5, 2), (2.5, 3), (2.4999, 2), (-0.5, 0), (-1.5, -1), (-1.6, -2)],
)
def test_js_round_rounds_halves_up(value: float, expected: int) -> None:
    assert js_round(value) == expected


@pytest.mark.parametrize(
    ("value", "locale", "expected"),
    [
        (1900, "en", "1,900"),
        (1900, "vi", "1.900"),
        (1900, "de", "1.900"),
        (1900, "fr", "1\u202f900"),  # narrow no-break space
        (1900, "ja", "1,900"),
        (999, "vi", "999"),
        (-1234, "vi", "-1.234"),
        (1234567, "en", "1,234,567"),
    ],
)
def test_format_int_groups_thousands(value: int, locale: str, expected: str) -> None:
    assert format_int(value, locale) == expected


@pytest.mark.parametrize(
    ("value", "locale", "expected"),
    [(2.0, "vi", "2,0"), (2.45, "en", "2.5"), (1.45, "en", "1.4"), (0.04, "de", "0,0")],
)
def test_format_dec1_matches_js_to_fixed(
    value: float, locale: str, expected: str
) -> None:
    assert format_dec1(value, locale) == expected


def test_day_names_start_on_monday_and_fall_back_to_english() -> None:
    assert day_name(0, "vi") == "T2"
    assert day_name(6, "en") == "Sun"
    assert day_name(6, "pt") == "Sun"


@pytest.mark.parametrize(
    ("days", "locale", "expected"),
    [
        ([0], "en", "Mon"),
        ([5, 6], "en", "Sat & Sun"),
        ([0, 2, 4], "en", "Mon, Wed & Fri"),
        ([5, 6], "ja", "土・日"),
        ([5, 6], "zh", "周六、周日"),
    ],
)
def test_join_days(days: list[int], locale: str, expected: str) -> None:
    assert join_days(days, locale) == expected


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        ([4, 5, 6], "Fri – Sun"),
        ([6, 4, 5], "Fri – Sun"),
        ([5, 6], "Sat & Sun"),
        ([4, 6], "Fri & Sun"),
        ([], ""),
    ],
)
def test_days_span_reads_three_consecutive_days_as_a_range(
    days: list[int], expected: str
) -> None:
    assert days_span(days, "en") == expected


def test_join_list() -> None:
    assert join_list(["Phở bò", "Cơm tấm"], "vi") == "Phở bò, Cơm tấm"
    assert join_list(["a", "b"], "zh") == "a、b"


@pytest.mark.parametrize(
    ("start", "locale", "expected"),
    [
        (date(2023, 10, 2), "vi", "2/10 – 8/10"),
        (date(2023, 10, 2), "en", "Oct 2 – Oct 8"),
        (date(2023, 10, 2), "de", "2.10. – 8.10."),
        (date(2023, 10, 2), "ja", "10/2 – 10/8"),
        (date(2023, 10, 30), "en", "Oct 30 – Nov 5"),
        (date(2023, 12, 25), "fr", "25/12 – 31/12"),
    ],
)
def test_format_range_is_monday_to_sunday(
    start: date, locale: str, expected: str
) -> None:
    assert format_range(start, locale) == expected


@pytest.mark.parametrize(
    ("hour", "locale", "expected"),
    [
        (22, "en", "10 PM"),
        (0, "en", "12 AM"),
        (12, "en", "12 PM"),
        (22, "vi", "22h"),
        (22, "fr", "22h"),
        (22, "es", "22:00"),
        (22, "de", "22 Uhr"),
        (22, "ja", "22時"),
        (22, "zh", "22点"),
    ],
)
def test_format_hour(hour: int, locale: str, expected: str) -> None:
    assert format_hour(hour, locale) == expected


@pytest.mark.parametrize(
    ("hour", "locale", "expected"),
    [
        (12, "vi", "12h–13h"),
        (12, "en", "12 PM–1 PM"),
        (23, "en", "11 PM–12 AM"),
        (12, "de", "12–13 Uhr"),
    ],
)
def test_format_hour_range(hour: int, locale: str, expected: str) -> None:
    assert format_hour_range(hour, locale) == expected


@pytest.mark.parametrize(
    ("minutes", "locale", "expected"),
    [
        (510, "en", "8:30 AM"),
        (1085, "en", "6:05 PM"),
        (510, "vi", "8h30"),
        (510, "fr", "8h30"),
        (510, "es", "8:30"),
        (510, "de", "8:30 Uhr"),
        (510, "ja", "8時30分"),
        (1085, "zh", "18点5分"),
        # Whole hours read like format_hour.
        (1080, "en", "6 PM"),
        (1080, "vi", "18h"),
        (0, "en", "12 AM"),
    ],
)
def test_format_time(minutes: int, locale: str, expected: str) -> None:
    assert format_time(minutes, locale) == expected
