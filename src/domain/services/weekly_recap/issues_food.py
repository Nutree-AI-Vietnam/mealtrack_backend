"""What and when the user ate or drank that held the week back."""

from __future__ import annotations

from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.format import js_round
from src.domain.services.weekly_recap.types import Card, Issue, Move
from src.domain.services.weekly_recap.viz import bar, card, dots


def _fiber_low(ctx: RecapContext) -> bool:
    stats = ctx.stats
    return stats.fiber_avg < stats.fiber_target * ctx.config.fiber_low_ratio


def _fiber_low_note(ctx: RecapContext) -> str:
    days = ctx.stats.lunch_no_veg_days
    if days < ctx.config.lunch_no_veg_days:
        return ctx.t("fiber_low.note")
    # Matches the plan's "n/d days": every day of the week had a bare lunch.
    every = days >= ctx.facts.week_days
    return ctx.t("fiber_low.note.lunch_all" if every else "fiber_low.note.lunch")


def _fiber_low_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    return card(
        "fiber",
        "eco",
        ctx.t("fiber_low.title"),
        count=js_round(stats.fiber_avg),
        suffix="g",
        unit=ctx.t("card.unit.grams_per_day", t=ctx.num(stats.fiber_target)),
        note=_fiber_low_note(ctx),
        viz=bar(stats.fiber_avg, stats.fiber_target),
    )


def _fiber_low_advice(ctx: RecapContext) -> Move:
    stats = ctx.stats
    gap = js_round(stats.fiber_target - stats.fiber_avg)
    if stats.lunch_no_veg_days > 0:
        why = ctx.t("fiber_low.why", n=stats.lunch_no_veg_days, d=ctx.facts.week_days)
    else:
        why = ctx.t("fiber_low.why.week")
    return Move(
        "fiber",
        "eco",
        ctx.t("fiber_low.advice"),
        ctx.t("fiber_low.impact", n=ctx.num(min(ctx.config.fiber_impact_cap_g, gap))),
        why,
        ctx.t("fiber_low.short"),
    )


def _water_low(ctx: RecapContext) -> bool:
    facts, target = ctx.facts, ctx.stats.water_target
    return facts.water_tracked and facts.water_avg < target * ctx.config.water_low_ratio


def _water_low_card(ctx: RecapContext) -> Card:
    facts, target = ctx.facts, ctx.stats.water_target
    return card(
        "water",
        "water_drop",
        ctx.t("water_low.title"),
        value=ctx.t("card.value.litres", v=ctx.dec(facts.water_avg)),
        unit=ctx.t("water_low.unit", v=ctx.dec(target)),
        note=ctx.t("water_low.note", n=facts.water_days_hit, d=facts.week_days),
        viz=bar(facts.water_avg, target),
    )


def _water_low_advice(ctx: RecapContext) -> Move:
    facts, target = ctx.facts, ctx.stats.water_target
    gap = min(ctx.config.water_impact_cap_l, target - facts.water_avg)
    return Move(
        "water",
        "water_drop",
        ctx.t("water_low.advice"),
        ctx.t("water_low.impact", v=ctx.dec(gap)),
        ctx.t("water_low.why", n=facts.water_days_hit, d=facts.week_days),
        ctx.t("water_low.short"),
    )


def _late_snack_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    nights = len(stats.late_snack_days)
    return card(
        "calm",
        "bedtime",
        ctx.t("late_snack.title"),
        count=nights,
        unit=ctx.t(
            "late_snack.unit",
            count=nights,
            hour=ctx.hour(ctx.config.late_snack_hour),
        ),
        note=ctx.t("late_snack.note", kcal=ctx.num(stats.late_snack_kcal)),
        viz=dots(stats.late_snack_days, "text"),
    )


def _late_snack_advice(ctx: RecapContext) -> Move:
    stats = ctx.stats
    nights = len(stats.late_snack_days)
    return Move(
        "calm",
        "bedtime",
        ctx.t("late_snack.advice", hour=ctx.hour(ctx.config.late_snack_hour)),
        ctx.t("late_snack.impact", kcal=ctx.num(stats.late_snack_kcal)),
        ctx.t("late_snack.why", count=nights, n=nights),
        ctx.t("late_snack.short"),
    )


def _sugary_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    return card(
        "fat",
        "local_cafe",
        ctx.t("sugary.title"),
        count=stats.sugary_drinks,
        unit=ctx.t("sugary.unit", count=stats.sugary_drinks),
        note=ctx.t("sugary.note", kcal=ctx.num(stats.sugary_kcal)),
    )


def _sugary_advice(ctx: RecapContext) -> Move:
    stats = ctx.stats
    drinks = stats.sugary_drinks
    return Move(
        "fat",
        "local_cafe",
        ctx.t("sugary.advice"),
        ctx.t("sugary.impact", kcal=ctx.num(stats.sugary_kcal / max(drinks, 1))),
        ctx.t("sugary.why", count=drinks, n=drinks),
        ctx.t("sugary.short"),
    )


FOOD_ISSUES: tuple[Issue, ...] = (
    Issue("fiber_low", 90, _fiber_low, _fiber_low_card, _fiber_low_advice),
    Issue("water_low", 80, _water_low, _water_low_card, _water_low_advice),
    Issue(
        "late_snack",
        85,
        lambda ctx: len(ctx.stats.late_snack_days) >= ctx.config.late_snack_days,
        _late_snack_card,
        _late_snack_advice,
    ),
    Issue(
        "sugary",
        75,
        lambda ctx: ctx.stats.sugary_drinks >= ctx.config.sugary_drinks,
        _sugary_card,
        _sugary_advice,
    ),
)
