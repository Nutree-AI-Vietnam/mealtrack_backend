"""What Nutree learned about a new or quiet logger, shown instead of wins."""

from __future__ import annotations

from src.domain.services.weekly_recap.compare import PROTEIN, trend
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.format import js_round
from src.domain.services.weekly_recap.insights_good import protein_stat
from src.domain.services.weekly_recap.types import Card, Insight
from src.domain.services.weekly_recap.viz import bar, card


def _learn_dishes(ctx: RecapContext) -> bool:
    stats = ctx.stats
    return stats.unique_dishes >= ctx.config.learn_dishes_min and (
        not ctx.facts.low_data or bool(stats.top_dishes)
    )


def _learn_dishes_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    if ctx.facts.low_data:
        top = stats.top_dishes[0]
        return card(
            "calm",
            "restaurant",
            ctx.t("learn_dishes.title.top"),
            value=top.name,
            unit=ctx.t("learn_dishes.unit.top", count=top.count, n=top.count),
            note=ctx.t("learn_dishes.note.top", kcal=ctx.num(top.kcal)),
        )
    names = [dish.name for dish in stats.top_dishes[:2]]
    return card(
        "calm",
        "restaurant",
        ctx.t("learn_dishes.title"),
        count=stats.unique_dishes,
        unit=ctx.t("learn_dishes.unit", count=stats.unique_dishes),
        note=ctx.t("learn_dishes.note", names=ctx.names(names)) if names else "",
    )


def _learn_protein(ctx: RecapContext) -> bool:
    average = ctx.facts.protein_avg
    target = ctx.stats.protein_target
    return average > 0 and average >= target * ctx.config.learn_protein_ratio


def _learn_protein_card(ctx: RecapContext) -> Card:
    average, target = ctx.facts.protein_avg, ctx.stats.protein_target
    key = "learn_protein.note.met" if average >= target else "learn_protein.note"
    return card(
        "protein",
        "egg_alt",
        ctx.t("learn_protein.title"),
        count=js_round(average),
        suffix="g",
        unit=ctx.t("card.unit.per_day_avg"),
        note=ctx.t(key, t=ctx.num(target)),
        viz=bar(average, target),
        trend=trend(ctx, PROTEIN, good_only=True),
    )


def _learn_hour_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    missed = stats.missed_meal
    if missed is not None:
        note = ctx.t("learn_hour.note.missed", meal=ctx.meal(missed.meal_type))
    else:
        note = ctx.t("learn_hour.note")
    return card(
        "water",
        "schedule",
        ctx.t("learn_hour.title"),
        value=ctx.hour_range(stats.common_log_hour or 0),
        unit=ctx.meal(stats.common_meal),
        note=note,
    )


LEARN: tuple[Insight, ...] = (
    Insight(
        "learn_dishes",
        50,
        _learn_dishes,
        _learn_dishes_card,
        lambda ctx: [
            str(ctx.stats.unique_dishes),
            ctx.t("stat.dishes", count=ctx.stats.unique_dishes),
        ],
    ),
    Insight("learn_protein", 45, _learn_protein, _learn_protein_card, protein_stat),
    Insight(
        "learn_hour",
        40,
        lambda ctx: ctx.stats.common_log_hour is not None,
        _learn_hour_card,
    ),
)
