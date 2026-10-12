"""Win cards for a full week.

``GOOD`` keeps the design engine's order, which breaks ties between equal
priorities. The rhythm cards (training, water, logging, streaks) live in
``insights_rhythm``.
"""

from __future__ import annotations

from typing import Any

from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.format import js_round
from src.domain.services.weekly_recap.insights_rhythm import (
    LOGGED_DAILY,
    MOVING,
    NEW_STREAK,
    WATER_HIT,
)
from src.domain.services.weekly_recap.types import Card, Insight
from src.domain.services.weekly_recap.viz import bar, card, spark, spark_max


def protein_stat(ctx: RecapContext) -> list[str]:
    """Share-card tile for the daily protein average."""
    return [f"{ctx.num(ctx.facts.protein_avg)}g", ctx.t("stat.protein")]


def _protein_spark(ctx: RecapContext) -> dict[str, Any]:
    stats, config = ctx.stats, ctx.config
    top = spark_max(
        stats.protein_by_day,
        stats.protein_target,
        config.spark_protein_headroom,
        config.spark_target_headroom,
    )
    return spark(stats.protein_by_day, top, stats.protein_target)


def _kcal_target(ctx: RecapContext) -> float:
    """Average target of the logged days, unrounded like the on-budget count."""
    kcal, targets = ctx.stats.kcal_by_day, ctx.facts.targets
    logged = [targets[day] for day in range(7) if kcal[day] > 0]
    return sum(logged) / len(logged) if logged else float(ctx.stats.budget)


def _kcal_spark(ctx: RecapContext, invert: bool) -> dict[str, Any]:
    """Daily calories, colored to agree with the on-budget count.

    Lower-is-better bars turn red only past the tolerance and, in a bulk
    week, are muted below the floor.
    """
    stats, config, facts = ctx.stats, ctx.config, ctx.facts
    target = _kcal_target(ctx)
    top = spark_max(
        stats.kcal_by_day,
        target,
        config.spark_kcal_headroom,
        config.spark_target_headroom,
    )
    if not invert:
        return spark(stats.kcal_by_day, top, target, targets=stats.budget_by_day)
    tolerance, budgets = config.on_budget_tolerance, stats.budget_by_day
    return spark(
        stats.kcal_by_day,
        top,
        target * tolerance,
        invert=True,
        targets=None if budgets is None else [day * tolerance for day in budgets],
        floors=facts.floors if facts.floor_ratio else None,
    )


def _protein_hit_card(ctx: RecapContext) -> Card:
    facts, target = ctx.facts, ctx.stats.protein_target
    over = facts.protein_avg >= target * ctx.config.protein_over_ratio
    return card(
        "protein",
        "egg_alt",
        ctx.t("protein_hit.title.over" if over else "protein_hit.title"),
        count=js_round(facts.protein_avg),
        suffix="g",
        unit=ctx.t("card.unit.per_day_avg"),
        note=ctx.t("protein_hit.note", n=facts.protein_days_hit, d=facts.week_days),
        viz=_protein_spark(ctx),
    )


def _on_budget(ctx: RecapContext) -> bool:
    # The card leads with the week's average, so it has to be on budget too.
    facts, config = ctx.facts, ctx.config
    target = _kcal_target(ctx)
    floor, ceiling = target * facts.floor_ratio, target * config.on_budget_tolerance
    return (
        facts.days_on_budget >= config.on_budget_days
        and floor <= facts.kcal_avg <= ceiling
    )


def _on_budget_card(ctx: RecapContext) -> Card:
    facts = ctx.facts
    return card(
        "calories",
        "local_fire_department",
        ctx.t("on_budget.title"),
        count=js_round(facts.kcal_avg),
        unit=ctx.t("card.unit.kcal_budget", kcal=ctx.num(ctx.stats.budget)),
        note=ctx.t("on_budget.note", n=facts.days_on_budget, d=facts.week_days),
        viz=_kcal_spark(ctx, invert=True),
    )


def _training_fuel(ctx: RecapContext) -> bool:
    stats, facts = ctx.stats, ctx.facts
    return (
        stats.goal == "bulk"
        and bool(facts.training_days)
        and stats.training_day_kcal >= stats.budget * ctx.config.training_fuel_ratio
        and facts.training_fueled * 2 > len(facts.training_days)
    )


def _training_fuel_card(ctx: RecapContext) -> Card:
    facts = ctx.facts
    return card(
        "fat",
        "bolt",
        ctx.t("training_fuel.title"),
        count=js_round(ctx.stats.training_day_kcal),
        unit=ctx.t("card.unit.kcal_budget", kcal=ctx.num(ctx.stats.budget)),
        note=ctx.t(
            "training_fuel.note",
            n=facts.training_fueled,
            d=len(facts.training_days),
        ),
        viz=_kcal_spark(ctx, invert=False),
    )


def _fiber_hit(ctx: RecapContext) -> bool:
    target = ctx.stats.fiber_target
    return target > 0 and ctx.stats.fiber_avg >= target * ctx.config.fiber_hit_ratio


def _fiber_hit_card(ctx: RecapContext) -> Card:
    stats = ctx.stats
    return card(
        "fiber",
        "eco",
        ctx.t("fiber_hit.title"),
        count=js_round(stats.fiber_avg),
        suffix="g",
        unit=ctx.t("card.unit.grams_per_day", t=ctx.num(stats.fiber_target)),
        note=ctx.t("fiber_hit.note"),
        viz=bar(stats.fiber_avg, stats.fiber_target),
    )


def _good_start(ctx: RecapContext) -> bool:
    facts, kcal = ctx.facts, ctx.stats.kcal_by_day
    floors, targets = facts.floors, facts.targets
    return (
        facts.first_day == 0
        and all(
            0 < kcal[day] and floors[day] <= kcal[day] <= targets[day] for day in (0, 1)
        )
        and facts.days_on_budget <= ctx.config.tough_on_budget_max
    )


def _good_start_card(ctx: RecapContext) -> Card:
    kcal = ctx.stats.kcal_by_day
    return card(
        "calories",
        "local_fire_department",
        ctx.t("good_start.title"),
        value=ctx.t("good_start.value", a=ctx.day(0), b=ctx.day(1)),
        unit=ctx.t("good_start.unit"),
        note=ctx.t("good_start.note", a=ctx.num(kcal[0]), b=ctx.num(kcal[1])),
        viz=_kcal_spark(ctx, invert=True),
    )


GOOD: tuple[Insight, ...] = (
    Insight(
        "protein_hit",
        90,
        lambda ctx: ctx.facts.protein_days_hit >= ctx.config.protein_hit_days,
        _protein_hit_card,
        protein_stat,
    ),
    Insight(
        "on_budget",
        85,
        _on_budget,
        _on_budget_card,
        lambda ctx: [
            f"{ctx.facts.days_on_budget}/{ctx.facts.week_days}",
            ctx.t("stat.on_budget"),
        ],
    ),
    Insight("training_fuel", 88, _training_fuel, _training_fuel_card),
    MOVING,
    WATER_HIT,
    Insight("fiber_hit", 65, _fiber_hit, _fiber_hit_card),
    LOGGED_DAILY,
    NEW_STREAK,
    Insight("good_start", 30, _good_start, _good_start_card),
)
