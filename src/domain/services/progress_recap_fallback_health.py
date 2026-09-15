"""Period health body and secondary watch beats."""

from __future__ import annotations

from src.domain.services.progress_recap_facts import RecapFacts
from src.domain.services.progress_recap_health import (
    pick_health_signal,
    sodium_is_high,
)
from src.domain.services.progress_recap_i18n import recap_text

_HEADLINE_KINDS = {
    "protein_low": frozenset({"protein", "protein_hits"}),
    "protein_high": frozenset({"protein", "protein_hits"}),
    "fat_high": frozenset({"fat"}),
    "salt_high": frozenset({"sodium"}),
    "sugar_high": frozenset({"quality"}),
    "water_low": frozenset({"hydration"}),
    "fiber_low": frozenset({"quality"}),
    "logged_over": frozenset({"pace"}),
    "logged_under": frozenset({"pace"}),
}


def _beat(kind: str, polarity: str, title: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "polarity": polarity, "title": title, "detail": detail}


def health_body(facts: RecapFacts, locale: str) -> str:
    if facts.horizon == "day":
        return recap_text(locale, "health_body_day")
    return recap_text(
        locale,
        "health_body_range",
        logged=facts.logged_days,
        total=facts.total_days,
    )


def sodium_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    return _beat(
        "sodium",
        "watch" if sodium_is_high(facts) else "win",
        recap_text(locale, "salt"),
        recap_text(locale, "salt_detail", sodium=facts.sodium_avg or 0),
    )


def finish_beats(
    facts: RecapFacts, locale: str, beats: list[dict[str, str]]
) -> list[dict[str, str]]:
    signal = pick_health_signal(facts)
    skip = _HEADLINE_KINDS.get(signal.key, frozenset())
    cleaned = [item for item in beats if item["kind"] not in skip]
    if sodium_is_high(facts) and signal.key != "salt_high":
        salt = sodium_beat(facts, locale)
        cleaned = [cleaned[0], salt, *cleaned[1:]] if cleaned else [salt]
    return cleaned[:3]
