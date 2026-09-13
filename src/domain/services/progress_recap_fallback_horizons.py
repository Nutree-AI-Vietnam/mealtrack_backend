"""Per-horizon fallback recap builders."""

from __future__ import annotations

from src.domain.services.progress_recap_facts import RecapFacts
from src.domain.services.progress_recap_fallback_bits import (
    closest_day_beat,
    fat_beat,
    health_headline,
    logging_beat,
    next_move_copy,
    pace_beat,
    protein_beat,
    quality_beat,
    steadiest_month_beat,
    steadiest_week_beat,
    water_beat,
    weekend_beat,
)
from src.domain.services.progress_recap_fallback_health import (
    finish_beats,
    health_body,
)

Copy = tuple[str, str, str, list[dict[str, str]]]


def day_copy(facts: RecapFacts, locale: str) -> Copy:
    return (
        health_headline(facts, locale),
        health_body(facts, locale),
        next_move_copy(facts, locale),
        finish_beats(
            facts,
            locale,
            [
                protein_beat(facts, locale, "protein"),
                water_beat(facts, locale),
                fat_beat(facts, locale) if facts.fat_avg > 0 else pace_beat(facts, locale),
            ],
        ),
    )


def week_copy(facts: RecapFacts, locale: str) -> Copy:
    win = closest_day_beat(facts, locale) if facts.best_day else logging_beat(facts, locale)
    return (
        health_headline(facts, locale),
        health_body(facts, locale),
        next_move_copy(facts, locale),
        finish_beats(
            facts,
            locale,
            [
                win,
                protein_beat(facts, locale, "protein_hits"),
                water_beat(facts, locale),
                fat_beat(facts, locale) if facts.fat_avg > 0 else logging_beat(facts, locale),
            ],
        ),
    )


def month_copy(facts: RecapFacts, locale: str) -> Copy:
    win = (
        steadiest_week_beat(facts, locale)
        if facts.best_week
        else logging_beat(facts, locale)
    )
    watch = weekend_beat(facts, locale) if facts.weekend_gap_kcal is not None else fat_beat(
        facts, locale
    )
    return (
        health_headline(facts, locale),
        health_body(facts, locale),
        next_move_copy(facts, locale),
        finish_beats(
            facts,
            locale,
            [
                win,
                watch,
                protein_beat(facts, locale, "protein_hits"),
                water_beat(facts, locale),
            ],
        ),
    )


def year_copy(facts: RecapFacts, locale: str) -> Copy:
    win = (
        steadiest_month_beat(facts, locale)
        if facts.best_month
        else pace_beat(facts, locale)
    )
    return (
        health_headline(facts, locale),
        health_body(facts, locale),
        next_move_copy(facts, locale),
        finish_beats(
            facts,
            locale,
            [
                win,
                quality_beat(facts, locale),
                water_beat(facts, locale),
                logging_beat(facts, locale),
            ],
        ),
    )
