"""How much the user ate against their targets: weekends, protein, training."""

from __future__ import annotations

from src.domain.services.weekly_recap.compare import PRE_WORKOUT_CARBS, PROTEIN, trend
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.format import js_round
from src.domain.services.weekly_recap.types import Card, Issue, Move
from src.domain.services.weekly_recap.viz import bar, card, dots

_MINUS = "−"
_DIETS = frozenset({"vegetarian", "vegan", "keto"})
# Meals logged before the weakest meal was kept get the design's breakfast tip.
_DEFAULT_PROTEIN_MEAL = "breakfast"


def _weekend_over(ctx: RecapContext) -> bool:
    delta = ctx.stats.weekend_delta
    return delta >= ctx.config.weekend_delta_kcal and bool(ctx.facts.weekend_over_days)


def _weekend_over_note(ctx: RecapContext) -> str:
    """The over days, and the biggest meal logged on them when known."""
    days = ctx.facts.weekend_over_days
    peaks = [peak for peak in ctx.stats.weekend_peaks if peak.day in days]
    if not peaks:
        return ctx.t("weekend_over.note", days=ctx.span(days))
    peak = max(peaks, key=lambda item: (item.kcal, -item.day))
    return ctx.t(
        "weekend_over.note.peak",
        days=ctx.span(days),
        dish=peak.name,
        kcal=ctx.num(peak.kcal),
    )


def _weekend_over_card(ctx: RecapContext) -> Card:
    # The card names only the days over target, so it averages just those.
    facts = ctx.facts
    days = facts.weekend_over_days
    return card(
        "calories",
        "weekend",
        ctx.t("weekend_over.title"),
        count=js_round(facts.weekend_over_kcal),
        prefix="+",
        unit=ctx.t("weekend_over.unit"),
        note=_weekend_over_note(ctx),
        viz=dots(days, "delta_down"),
    )


def _weekend_over_advice(ctx: RecapContext) -> Move:
    return Move(
        "carbs",
        "photo_camera",
        ctx.t("weekend_over.advice"),
        ctx.t("weekend_over.impact"),
        ctx.t("weekend_over.why"),
        ctx.t("weekend_over.short"),
    )


def _protein_low(ctx: RecapContext) -> bool:
    facts, config = ctx.facts, ctx.config
    return (
        facts.protein_days_hit <= config.protein_low_days_max
        and facts.meals_logged >= config.protein_low_min_meals
        and facts.protein_avg < ctx.stats.protein_target
    )


def _protein_low_card(ctx: RecapContext) -> Card:
    facts, target = ctx.facts, ctx.stats.protein_target
    return card(
        "protein",
        "egg_alt",
        ctx.t("protein_low.title"),
        count=js_round(facts.protein_avg),
        suffix="g",
        unit=ctx.t("card.unit.grams_per_day", t=ctx.num(target)),
        note=ctx.t("protein_low.note", n=facts.protein_days_hit, d=facts.week_days),
        viz=bar(facts.protein_avg, target),
        trend=trend(ctx, PROTEIN),
    )


def _protein_low_advice(ctx: RecapContext) -> Move:
    """Protein foods the user's diet allows, for their lowest-protein meal."""
    stats = ctx.stats
    gap = ctx.num(stats.protein_target - ctx.facts.protein_avg)
    weakest = stats.low_protein_meal
    meal = ctx.meal(weakest or _DEFAULT_PROTEIN_MEAL)
    key = f"protein_low.advice.{stats.diet}" if stats.diet in _DIETS else None
    if weakest is not None:
        why = ctx.t("protein_low.why.meal", n=gap, meal=meal)
    else:
        why = ctx.t("protein_low.why", n=gap)
    return Move(
        "protein",
        "egg_alt",
        ctx.t(key or "protein_low.advice", meal=meal),
        ctx.t("protein_low.impact", n=ctx.num(ctx.config.protein_impact_g)),
        why,
        ctx.t("protein_low.short"),
    )


def _rest_under(ctx: RecapContext) -> bool:
    rest_days = ctx.facts.rest_under_days
    return ctx.stats.goal == "bulk" and len(rest_days) >= ctx.config.rest_under_days


def _rest_under_card(ctx: RecapContext) -> Card:
    facts = ctx.facts
    return card(
        "calories",
        "weekend",
        ctx.t("rest_under.title"),
        count=js_round(facts.rest_shortfall),
        prefix=_MINUS,
        unit=ctx.t("rest_under.unit", days=ctx.days(facts.rest_under_days)),
        note=ctx.t("rest_under.note"),
        viz=dots(facts.rest_under_days, "delta_down"),
    )


def _rest_under_advice(ctx: RecapContext) -> Move:
    facts, step = ctx.facts, ctx.config.rest_impact_step_kcal
    shortfall = facts.rest_shortfall
    snack = js_round(shortfall / step) * step if step > 0 else shortfall
    return Move(
        "fat",
        "bakery_dining",
        ctx.t("rest_under.advice"),
        ctx.t("rest_under.impact", kcal=ctx.num(snack)),
        ctx.t(
            "rest_under.why",
            days=ctx.days(facts.rest_under_days),
            kcal=ctx.num(shortfall),
        ),
        ctx.t("rest_under.short"),
    )


def _preworkout_carbs(ctx: RecapContext) -> bool:
    stats = ctx.stats
    carbs = stats.pre_workout_carbs
    ceiling = stats.carbs_target * ctx.config.preworkout_carbs_ratio
    return stats.goal == "bulk" and carbs is not None and carbs < ceiling


def _preworkout_carbs_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    carbs = stats.pre_workout_carbs or 0.0
    return card(
        "carbs",
        "grain",
        ctx.t("preworkout_carbs.title"),
        count=js_round(carbs),
        suffix="g",
        unit=ctx.t("preworkout_carbs.unit", t=ctx.num(stats.carbs_target)),
        note=ctx.t("preworkout_carbs.note"),
        viz=bar(carbs, stats.carbs_target),
        trend=trend(ctx, PRE_WORKOUT_CARBS),
    )


def _preworkout_carbs_advice(ctx: RecapContext) -> Move:
    stats = ctx.stats
    carbs = stats.pre_workout_carbs or 0.0
    return Move(
        "carbs",
        "grain",
        ctx.t("preworkout_carbs.advice"),
        ctx.t("preworkout_carbs.impact", n=ctx.num(stats.carbs_target - carbs)),
        ctx.t(
            "preworkout_carbs.why",
            n=ctx.num(carbs),
            t=ctx.num(stats.carbs_target),
        ),
        ctx.t("preworkout_carbs.short"),
    )


LOAD_ISSUES: tuple[Issue, ...] = (
    Issue(
        "weekend_over",
        88,
        _weekend_over,
        _weekend_over_card,
        _weekend_over_advice,
    ),
    Issue("protein_low", 70, _protein_low, _protein_low_card, _protein_low_advice),
    Issue("rest_under", 90, _rest_under, _rest_under_card, _rest_under_advice),
    Issue(
        "preworkout_carbs",
        70,
        _preworkout_carbs,
        _preworkout_carbs_card,
        _preworkout_carbs_advice,
    ),
)
