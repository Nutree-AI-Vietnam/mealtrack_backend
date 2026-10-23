"""This week against the week before, for the trend line on a card.

``WeekStats.last_week`` holds the previous week's numbers, read from its
stored recap. A trend says which way a card's number moved since then and
whether that is good. Counts (nights, drinks, days) only compare between
weeks of the same length; averages compare across any two weeks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.domain.model.weekly_recap import LastWeek
from src.domain.services.weekly_recap.context import RecapContext

# Float noise must not hide a change of exactly one step (2.2 L - 2.0 L).
_EPSILON = 1e-9


@dataclass(frozen=True)
class Metric:
    """One number a card shows, read from this week and from last week.

    ``shown`` formats a value the way the card writes it, and ``step`` is
    the smallest change worth pointing out.
    """

    now: Callable[[RecapContext], float | None]
    last: Callable[[LastWeek], float | None]
    shown: Callable[[RecapContext, float], str]
    step: float
    higher_is_better: bool = True
    count: bool = False


def _grams(ctx: RecapContext, value: float) -> str:
    return f"{ctx.num(value)}g"


def _litres(ctx: RecapContext, value: float) -> str:
    return ctx.t("card.value.litres", v=ctx.dec(value))


def _whole(ctx: RecapContext, value: float) -> str:
    return ctx.num(value)


def _positive(value: float | None) -> float | None:
    """An average, or None when nothing was logged to average."""
    return value if value is not None and value > 0 else None


def _water_now(ctx: RecapContext) -> float | None:
    return ctx.facts.water_avg if ctx.facts.water_tracked else None


PROTEIN = Metric(
    lambda ctx: _positive(ctx.facts.protein_avg),
    lambda last: _positive(last.protein_avg),
    _grams,
    step=5,
)
FIBER = Metric(
    lambda ctx: _positive(ctx.stats.fiber_avg),
    lambda last: _positive(last.fiber_avg),
    _grams,
    step=2,
)
WATER = Metric(_water_now, lambda last: last.water_avg, _litres, step=0.2)
ON_BUDGET = Metric(
    lambda ctx: ctx.facts.days_on_budget,
    lambda last: last.days_on_budget,
    _whole,
    step=1,
    count=True,
)
WORKOUTS = Metric(
    lambda ctx: len(ctx.stats.workouts),
    lambda last: last.workouts,
    _whole,
    step=1,
    count=True,
)
LATE_SNACKS = Metric(
    lambda ctx: len(ctx.stats.late_snack_days),
    lambda last: last.late_snack_nights,
    _whole,
    step=1,
    higher_is_better=False,
    count=True,
)
SUGARY_DRINKS = Metric(
    lambda ctx: ctx.stats.sugary_drinks,
    lambda last: last.sugary_drinks,
    _whole,
    step=1,
    higher_is_better=False,
    count=True,
)
WEEKEND_OVER = Metric(
    lambda ctx: len(ctx.facts.weekend_over_days),
    lambda last: last.weekend_over_days,
    _whole,
    step=1,
    higher_is_better=False,
    count=True,
)
REST_UNDER = Metric(
    lambda ctx: len(ctx.facts.rest_under_days),
    lambda last: last.rest_under_days,
    _whole,
    step=1,
    higher_is_better=False,
    count=True,
)
PRE_WORKOUT_CARBS = Metric(
    lambda ctx: ctx.stats.pre_workout_carbs,
    lambda last: last.pre_workout_carbs,
    _grams,
    step=5,
)


def compared(ctx: RecapContext, metric: Metric) -> tuple[float, float] | None:
    """This week's and last week's value, when the two weeks compare."""
    last = ctx.stats.last_week
    if last is None:
        return None
    if metric.count and last.week_days != ctx.facts.week_days:
        return None
    now, before = metric.now(ctx), metric.last(last)
    if now is None or before is None:
        return None
    return float(now), float(before)


def improved(metric: Metric, now: float, before: float) -> bool:
    """Whether the number moved at least a step the good way."""
    delta = now - before if metric.higher_is_better else before - now
    return delta + _EPSILON >= metric.step


def trend(
    ctx: RecapContext, metric: Metric, good_only: bool = False
) -> dict[str, Any] | None:
    """The card's trend line, or None without a change worth a mention.

    ``up`` says which way the number moved, ``good`` whether that helps.
    Win cards pass ``good_only``: a slip from a week that still went well
    is not worth a red line on the card that celebrates it.
    """
    pair = compared(ctx, metric)
    if pair is None:
        return None
    now, before = pair
    if abs(now - before) + _EPSILON < metric.step:
        return None
    up = now > before
    good = up == metric.higher_is_better
    if good_only and not good:
        return None
    key = "trend.up" if up else "trend.down"
    return {"text": ctx.t(key, v=metric.shown(ctx, before)), "up": up, "good": good}
