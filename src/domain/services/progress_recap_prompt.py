"""Prompts for horizon-scoped progress recap copy."""

from __future__ import annotations

from src.domain.services.progress_recap_facts import RecapFacts
from src.domain.services.progress_recap_i18n import recap_locale
from src.domain.services.prompts.prompt_constants import LANGUAGE_NAMES

ALLOWED_KINDS = {
    "day": ("pace", "protein", "fat", "hydration", "sodium", "next_move"),
    "week": (
        "pace",
        "consistency",
        "protein_hits",
        "fat",
        "hydration",
        "sodium",
        "best_day",
        "next_move",
    ),
    "month": (
        "pace",
        "weekend_gap",
        "protein_hits",
        "fat",
        "hydration",
        "sodium",
        "best_week",
        "consistency",
        "next_move",
    ),
    "year": (
        "pace",
        "consistency",
        "best_month",
        "quality",
        "hydration",
        "sodium",
        "next_move",
    ),
}

SYSTEM_PROMPT = """You write Nutree Progress recaps.
Rules:
- Use only the supplied facts. Never invent numbers, days, or trends.
- No medical advice, diagnoses, or shame.
- Write like a knowledgeable friend. Short. Specific. You-voice.
- One idea per field. Never repeat the same finding in headline, body, next_move, and highlights.
- Headline: one finding, one number. Example: "Protein is short — 50 g to go."
- Body: only where the finding comes from. Day = "From today's meals." Week/month/year = "From X of Y days you logged." Do not restate the headline issue or its number.
- next_move: the action for THIS horizon, no second number. Day = tonight. Week = on logged days. Month = weekday meals. Year = the main meal. Never say "next meal" on week, month, or year.
- If sodium_avg is at or over 2300 and sodium is NOT the headline, put it in one highlight only. Soften as "a bit high".
- If sodium IS the headline, do not also add a sodium highlight.
- If sugar_avg is at or over 50 and not the headline, one sugar/quality highlight is enough.
- Never treat unlogged days as a calorie deficit. Unused weekly budget is not "under target".
- Never show ISO dates or week numbers like 2026-W36. Use weekday + month names in the user language.
- Highlights must not repeat the headline kind (no protein highlight if headline is protein).
- Exactly 3 highlights. Prefer one win, one other watch, one logging or closest-day fact.
- Each highlight.kind must be in allowed_kinds.
- highlight.title <= 28 chars. highlight.detail <= 90 chars and must include one fact number.
- polarity is win, watch, or next. Judge each highlight on its own metric.
- Hydration below 85% of goal is watch, never win. 0 ml is always watch.
- headline <= 90 chars. body <= 180 chars. next_move <= 90 chars.
- Return JSON only. JSON keys stay English.
"""


def build_system_prompt(locale: str) -> str:
    code, name = _language(locale)
    return (
        SYSTEM_PROMPT
        + f"- Write headline, body, next_move, and every highlight title/detail "
        f"in {name} ({code}) only. Use one language. Do not mix English unless "
        f"{name} is English.\n"
    )


def build_user_prompt(facts: RecapFacts, locale: str) -> str:
    code, name = _language(locale)
    kinds = ", ".join(ALLOWED_KINDS[facts.horizon])
    return (
        f"Language: {name} ({code}). Write every user-facing string in {name} only.\n"
        f"Horizon: {facts.horizon} ({facts.start} to {facts.end})\n"
        f"Allowed kinds: {kinds}\n"
        f"Facts: {facts.to_prompt_dict()}\n"
        "Write a recap for this horizon only. Do not repeat the headline in the body."
    )


def _language(locale: str) -> tuple[str, str]:
    code = recap_locale(locale)
    return code, LANGUAGE_NAMES.get(code, "English")
