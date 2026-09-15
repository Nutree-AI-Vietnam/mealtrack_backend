"""Horizon-specific recap copy when AI is unavailable."""

from __future__ import annotations

from src.domain.services.progress_recap_facts import RecapFacts
from src.domain.services.progress_recap_fallback_horizons import (
    day_copy,
    month_copy,
    week_copy,
    year_copy,
)
from src.domain.services.progress_recap_prompt import ALLOWED_KINDS


def fallback_copy(
    facts: RecapFacts, locale: str = "en"
) -> tuple[str, str, str, list[dict[str, str]]]:
    headline, body, next_move, highlights = {
        "day": day_copy,
        "week": week_copy,
        "month": month_copy,
        "year": year_copy,
    }[facts.horizon](facts, locale)
    allowed = ALLOWED_KINDS[facts.horizon]
    return (
        headline,
        body,
        next_move,
        [item for item in highlights if item["kind"] in allowed][:3],
    )
