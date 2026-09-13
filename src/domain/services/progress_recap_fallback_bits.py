"""Shared health headline, body, next-move, and beats."""

from __future__ import annotations

from src.domain.services.progress_recap_facts import RecapFacts
from src.domain.services.progress_recap_health import (
    fat_calorie_share,
    fat_is_high,
    pick_health_signal,
    protein_gap,
    protein_over,
    water_is_low,
)
from src.domain.services.progress_recap_i18n import recap_text
from src.domain.services.progress_recap_labels import (
    friendly_day,
    friendly_month,
    friendly_week,
)


def beat(kind: str, polarity: str, title: str, detail: str) -> dict[str, str]:
    return {"kind": kind, "polarity": polarity, "title": title, "detail": detail}


def health_headline(facts: RecapFacts, locale: str) -> str:
    signal = pick_health_signal(facts)
    return recap_text(
        locale,
        signal.key,
        protein=facts.protein_avg,
        protein_target=facts.protein_target_avg,
        gap=protein_gap(facts),
        fiber=facts.fiber_avg,
        fiber_target=facts.fiber_target_avg,
        fiber_gap=max(facts.fiber_target_avg - facts.fiber_avg, 0.0),
        sodium=facts.sodium_avg or 0,
    )


def next_move_copy(facts: RecapFacts, locale: str) -> str:
    return recap_text(
        locale,
        _NEXT[pick_health_signal(facts).key],
        when=recap_text(locale, f"when_{facts.horizon}"),
    )


def protein_beat(facts: RecapFacts, locale: str, kind: str) -> dict[str, str]:
    signal = pick_health_signal(facts)
    if signal.key == "protein_low":
        key, polarity = "protein_low_detail", "watch"
        gap = protein_gap(facts)
    elif signal.key == "protein_high":
        key, polarity = "protein_high_detail", "watch"
        gap = protein_over(facts)
    else:
        key, polarity = "protein_ok_detail", "win"
        gap = 0
    return beat(
        kind,
        polarity,
        recap_text(locale, "protein"),
        recap_text(
            locale,
            key,
            protein=facts.protein_avg,
            protein_target=facts.protein_target_avg,
            gap=gap,
        ),
    )


def fat_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    return beat(
        "fat",
        "watch" if fat_is_high(facts) else "win",
        recap_text(locale, "fat"),
        recap_text(locale, "fat_high_detail", share=fat_calorie_share(facts)),
    )


def water_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    return beat(
        "hydration",
        "watch" if water_is_low(facts) else "win",
        recap_text(locale, "water"),
        recap_text(
            locale,
            "water_detail",
            water=facts.hydration_avg,
            water_goal=facts.hydration_goal_avg,
        ),
    )


def pace_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    signal = pick_health_signal(facts)
    return beat(
        "pace",
        signal.polarity,
        recap_text(locale, "logging"),
        recap_text(
            locale,
            "logging_detail",
            logged=facts.logged_days,
            total=facts.total_days,
        ),
    )


def logging_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    return beat(
        "consistency",
        "win",
        recap_text(locale, "logging"),
        recap_text(
            locale, "logging_detail", logged=facts.logged_days, total=facts.total_days
        ),
    )


def closest_day_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    return beat(
        "best_day",
        "win",
        recap_text(locale, "closest_day"),
        recap_text(
            locale, "closest_day_detail", day=friendly_day(facts.best_day, locale)
        ),
    )


def weekend_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    gap = facts.weekend_gap_kcal or 0
    key = "weekend_above" if gap > 0 else "weekend_below"
    return beat(
        "weekend_gap",
        "watch" if abs(gap) >= 150 else "win",
        recap_text(locale, "weekend_gap"),
        recap_text(locale, key),
    )


def quality_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    watch = pick_health_signal(facts).key in {"fiber_low", "salt_high", "sugar_high"}
    return beat(
        "quality",
        "watch" if watch else "win",
        recap_text(locale, "quality"),
        recap_text(locale, "quality_watch" if watch else "quality_ok"),
    )


def steadiest_week_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    return beat(
        "best_week",
        "win",
        recap_text(locale, "steadiest_week"),
        recap_text(
            locale, "steadiest_week_detail", week=friendly_week(facts.best_week, locale)
        ),
    )


def steadiest_month_beat(facts: RecapFacts, locale: str) -> dict[str, str]:
    return beat(
        "best_month",
        "win",
        recap_text(locale, "steadiest_month"),
        recap_text(
            locale,
            "steadiest_month_detail",
            month=friendly_month(facts.best_month, locale),
        ),
    )


_NEXT = {
    "protein_low": "next_add_protein",
    "protein_high": "next_ease_protein",
    "fat_high": "next_cut_fat",
    "fiber_low": "next_add_fiber",
    "water_low": "next_add_water",
    "salt_high": "next_cut_salt",
    "sugar_high": "next_cut_sugar",
    "logged_over": "next_ease_calories",
    "logged_under": "next_add_calories",
    "on_track": "next_repeat",
}
