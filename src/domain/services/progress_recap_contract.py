"""Validate AI recap JSON and build a deterministic fallback."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.domain.services.progress_recap_facts import RecapFacts
from src.domain.services.progress_recap_fallback import fallback_copy
from src.domain.services.progress_recap_health import water_is_low
from src.domain.services.progress_recap_prompt import ALLOWED_KINDS

_POLARITIES = frozenset({"win", "watch", "next"})


class RecapHighlightModel(BaseModel):
    kind: str
    polarity: str
    title: str
    detail: str


class RecapAiOutput(BaseModel):
    headline: str
    body: str = ""
    next_move: str = ""
    highlights: list[RecapHighlightModel] = Field(default_factory=list)


def empty_recap(facts: RecapFacts) -> dict[str, Any]:
    return _payload(
        facts, status="empty", headline="", body="", next_move="", highlights=[]
    )


def missing_recap(horizon: str, start: str, end: str) -> dict[str, Any]:
    return {
        "status": "missing",
        "horizon": horizon,
        "effective_start": start,
        "effective_end": end,
        "headline": "",
        "body": "",
        "next_move": "",
        "highlights": [],
        "generated_at": None,
    }


def parse_ai_recap(raw: Any, facts: RecapFacts) -> dict[str, Any] | None:
    parsed = _coerce(raw)
    if parsed is None:
        return None
    allowed = ALLOWED_KINDS[facts.horizon]
    highlights = [
        _correct_polarity(facts, item)
        for item in parsed.highlights
        if item.kind in allowed and item.polarity in _POLARITIES
    ][:3]
    if len(highlights) < 2:
        return None
    return _payload(
        facts,
        status="ready",
        headline=_clip(parsed.headline, 90),
        body=_clip(parsed.body, 180),
        next_move=_clip(parsed.next_move, 90),
        highlights=[_highlight(item) for item in highlights],
    )


def fallback_recap(facts: RecapFacts, locale: str = "en") -> dict[str, Any]:
    if facts.logged_days == 0:
        return empty_recap(facts)
    headline, body, next_move, highlights = fallback_copy(facts, locale)
    return _payload(
        facts,
        status="ready",
        headline=_clip(headline, 90),
        body=_clip(body, 180),
        next_move=_clip(next_move, 90),
        highlights=[
            {
                "kind": item["kind"],
                "polarity": item["polarity"],
                "title": _clip(item["title"], 28),
                "detail": _clip(item["detail"], 90),
            }
            for item in _correct_polarity_dicts(facts, highlights)
        ],
    )


def _coerce(raw: Any) -> RecapAiOutput | None:
    if not isinstance(raw, dict):
        return None
    try:
        return RecapAiOutput.model_validate(raw)
    except Exception:
        return None


def _correct_polarity(facts: RecapFacts, item: RecapHighlightModel) -> RecapHighlightModel:
    if item.kind == "hydration" and water_is_low(facts) and item.polarity == "win":
        return item.model_copy(update={"polarity": "watch"})
    return item


def _correct_polarity_dicts(
    facts: RecapFacts, highlights: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [
        {**item, "polarity": "watch"}
        if item["kind"] == "hydration" and water_is_low(facts) and item["polarity"] == "win"
        else item
        for item in highlights
    ]


def _highlight(item: RecapHighlightModel) -> dict[str, str]:
    return {
        "kind": item.kind,
        "polarity": item.polarity,
        "title": _clip(item.title, 28),
        "detail": _clip(item.detail, 90),
    }


def _clip(value: str, max_length: int) -> str:
    text = " ".join((value or "").split())
    if text:
        text = text[0].upper() + text[1:]
    return text[:max_length].rstrip()


def _payload(
    facts: RecapFacts,
    *,
    status: str,
    headline: str,
    body: str,
    next_move: str,
    highlights: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "status": status,
        "horizon": facts.horizon,
        "effective_start": facts.start,
        "effective_end": facts.end,
        "headline": headline,
        "body": body,
        "next_move": next_move,
        "highlights": highlights,
        "generated_at": None,
    }
