"""The recap engine against the Claude Design engine's own sample weeks.

The fixture is ``generate(SAMPLES)`` from the design's wrap-engine.js
(Vietnamese copy), normalized to the app's payload shape, with the changes
listed in its ``notes``. Every other locale renders the same weeks to check
the copy tables, not the numbers.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from tests.fixtures.weekly_recap_samples import GOLDEN, SAMPLES, sample_stats

from src.domain.services.weekly_recap import generate_recap
from src.domain.services.weekly_recap.cases import CASES as STORIES
from src.domain.services.weekly_recap.strings import TABLES

SECTIONS = ("intro", "wins", "grow", "plan", "share")
# Keys whose string values never change with the locale.
CODES = frozenset({"case_id", "week_start", "slides", "kind", "icon", "type", "color"})
TOLERANCE = 0.011


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _diff(actual: Any, expected: Any, path: str) -> list[str]:
    """Every difference between two payloads, numbers within TOLERANCE."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        out: list[str] = []
        for key in sorted(set(actual) | set(expected)):
            if key not in actual:
                out.append(f"{path}.{key}: missing")
            elif key not in expected:
                out.append(f"{path}.{key}: unexpected {actual[key]!r}")
            else:
                out.extend(_diff(actual[key], expected[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        if len(actual) != len(expected):
            return [f"{path}: {len(actual)} items, expected {len(expected)}"]
        return [
            line
            for index, pair in enumerate(zip(actual, expected, strict=True))
            for line in _diff(*pair, f"{path}[{index}]")
        ]
    if _is_number(actual) and _is_number(expected):
        if abs(actual - expected) <= TOLERANCE:
            return []
        return [f"{path}: {actual} != {expected}"]
    if type(actual) is type(expected) and actual == expected:
        return []
    return [f"{path}: {actual!r} != {expected!r}"]


def _render(name: str, locale: str) -> dict[str, Any]:
    return generate_recap(sample_stats(name), locale=locale)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _skeleton(value: Any, key: str = "") -> Any:
    """``value`` with its localized words blanked out."""
    if isinstance(value, dict):
        return {name: _skeleton(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_skeleton(item, key) for item in value]
    if isinstance(value, str) and key not in CODES:
        return ""
    return value


def test_samples_cover_every_story() -> None:
    assert sorted(SAMPLES) == sorted(STORIES)


@pytest.mark.parametrize("name", list(SAMPLES))
def test_matches_design_engine(name: str) -> None:
    payload = _render(name, GOLDEN["locale"])

    assert _diff(payload, SAMPLES[name]["expected"], name) == []


def test_diff_reports_every_change() -> None:
    expected = SAMPLES["steady"]["expected"]
    changed = copy.deepcopy(expected)
    changed["intro"]["big"] += 1
    changed["wins"]["cards"][0]["viz"]["vals"][0] += 0.5
    changed["plan"]["moves"][0]["title"] = "?"
    del changed["share"]["foot"]

    assert len(_diff(changed, expected, "steady")) == 4


@pytest.mark.parametrize("locale", sorted(TABLES))
@pytest.mark.parametrize("name", list(SAMPLES))
def test_renders_every_locale(name: str, locale: str) -> None:
    payload = _render(name, locale)

    json.dumps(payload)
    assert payload["case_id"] == name
    assert payload["slides"] == [key for key in SECTIONS if payload[key]]
    assert {"intro", "plan"} <= set(payload["slides"])
    assert isinstance(payload["intro"]["big"], int)
    for text in _strings(payload):
        assert "{" not in text and "}" not in text, text
    for key in ("eyebrow", "title", "sub", "label", "big_unit"):
        assert payload["intro"][key]
    assert all(move["title"] and move["why"] for move in payload["plan"]["moves"])
    share = payload["share"]
    if share is not None:
        assert 1 <= len(share["stats"]) <= 3
        assert all(len(tile) == 2 and all(tile) for tile in share["stats"])
        if share["progress"] is not None:
            assert 0 <= share["progress"]["pct"] <= 1


@pytest.mark.parametrize("locale", sorted(set(TABLES) - {"vi"}))
@pytest.mark.parametrize("name", list(SAMPLES))
def test_locale_changes_only_words(name: str, locale: str) -> None:
    assert _skeleton(_render(name, locale)) == _skeleton(_render(name, "vi"))
