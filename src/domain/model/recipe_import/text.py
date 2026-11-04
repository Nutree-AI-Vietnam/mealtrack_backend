"""Text clean-up and parsing helpers for recipe sources (no I/O)."""

from __future__ import annotations

import html
import math
import re
from typing import Any

from src.domain.model.recipe_import.limits import (
    MAX_MINUTES,
    MAX_SERVINGS,
    MIN_SERVINGS,
)

_TAG = re.compile(r"<[^>]{0,2000}>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INLINE_SPACE = re.compile(r"[ \t ​  ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+(?:\.\d+)?)D)?"
    r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$",
    re.IGNORECASE,
)
_HOURS_MINUTES = re.compile(
    r"(?:(?P<hours>\d+(?:[.,]\d+)?)\s*(?:h|hr|hrs|hour|hours|giờ|tiếng))?\s*"
    r"(?:(?P<minutes>\d+)\s*(?:m|min|mins|minute|minutes|phút))?",
    re.IGNORECASE,
)
_FIRST_INT = re.compile(r"\d{1,4}")


def clean_text(value: Any, *, max_chars: int, multiline: bool = False) -> str | None:
    """Unescape HTML, drop tags and control characters, collapse spacing."""
    if value is None:
        return None
    if not isinstance(value, str):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        else:
            return None
    text = html.unescape(html.unescape(value))
    text = _TAG.sub(" ", text)
    text = _CONTROL.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    if multiline:
        lines = [_INLINE_SPACE.sub(" ", line).strip() for line in text.split("\n")]
        text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    else:
        text = _INLINE_SPACE.sub(" ", text.replace("\n", " ")).strip()
    if not text:
        return None
    return truncate(text, max_chars)


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rstrip()
    # Prefer ending on a word boundary when one is close.
    space = cut.rfind(" ", max(0, max_chars - 40))
    return (cut[:space] if space > 0 else cut).rstrip() + "…"


def parse_duration_minutes(value: Any) -> int | None:
    """Minutes from an ISO-8601 duration ("PT1H30M"), a number, or "1 h 20 min"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _bounded_minutes(float(value))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    iso = _ISO_DURATION.match(text)
    if iso and any(iso.groupdict().values()):
        parts = {k: float(v) if v else 0.0 for k, v in iso.groupdict().items()}
        total = (
            parts["days"] * 1440
            + parts["hours"] * 60
            + parts["minutes"]
            + parts["seconds"] / 60
        )
        return _bounded_minutes(total)
    for human in _HOURS_MINUTES.finditer(text):
        if human.group("hours") or human.group("minutes"):
            hours = float((human.group("hours") or "0").replace(",", "."))
            minutes = float(human.group("minutes") or 0)
            return _bounded_minutes(hours * 60 + minutes)
    if text.isdigit():
        return _bounded_minutes(float(text))
    return None


def parse_servings(value: Any) -> int | None:
    """First plausible serving count from "4", "Serves 4-6", ["4", "4 servings"]."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            parsed = parse_servings(item)
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return clamp_servings(int(round(value)))
    if isinstance(value, str):
        match = _FIRST_INT.search(value)
        if match:
            return clamp_servings(int(match.group()))
    return None


def clamp_servings(value: int | None) -> int | None:
    if value is None or not MIN_SERVINGS <= value <= MAX_SERVINGS:
        return None
    return value


def _bounded_minutes(total: float) -> int | None:
    if not math.isfinite(total) or total <= 0:
        return None
    minutes = int(round(total))
    if minutes > MAX_MINUTES:
        return None
    return max(minutes, 1)
