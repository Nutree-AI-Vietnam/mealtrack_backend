"""Recap copy lookup: placeholders, singular forms, English fallback."""

from __future__ import annotations

import pytest

from src.domain.services.weekly_recap.i18n import is_singular, text
from src.domain.services.weekly_recap.strings import TABLES


def test_fills_placeholders() -> None:
    assert text("en", "intro.eyebrow.week", n=14) == "Week 14 with Nutree"


def test_count_picks_the_singular_form_without_filling_n() -> None:
    assert text("en", "wins.title", count=1, n=1) == "1 highlight this week."
    assert text("en", "wins.title", count=2, n=2) == "2 highlights this week."
    with pytest.raises(KeyError):
        text("en", "wins.title", count=1)


@pytest.mark.parametrize(
    ("locale", "count", "expected"),
    [
        ("en", 1, True),
        ("en", 0, False),
        ("en", 2, False),
        ("de", 1, True),
        ("es", 1, True),
        ("fr", 0, True),
        ("fr", 1, True),
        ("fr", 2, False),
        ("vi", 1, False),
        ("ja", 1, False),
        ("zh", 1, False),
        ("en", None, False),
    ],
)
def test_is_singular(locale: str, count: int | None, expected: bool) -> None:
    assert is_singular(locale, count) is expected


def test_unknown_locale_uses_english() -> None:
    assert text("pt", "plan.title.1") == text("en", "plan.title.1")


def test_missing_key_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TABLES, "vi", {})

    assert text("vi", "plan.title.1") == "Next week, try 1 thing."


def test_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        text("en", "no.such.key")
