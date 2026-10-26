"""This week against the week before, for the trend line on a card.

``WeekStats.last_week`` holds the previous week's numbers, read from its
stored recap. A trend says which way a card's number moved since then and
whether that is good.

A number only compares when both weeks logged the same number of days
that feed it (its coverage): a day nobody logged has no late snacks and no
water, and fewer logged days must never read as progress.

A logged day looks fully logged with two meal types (a lunch logged as two
dishes is one) and ``full_day_ratio`` of its calorie target; any other
logged day is thin. A thin day pulls an average down and keeps a day under
budget, so averages over logged days (protein, fiber) need the same number
of thin days, days on budget the same days logged and thin days, and a
weekend day counts once it went over or is fully logged. Late snacks need
meals logged live (a meal logged a day later has no time of day).

Counts against the calorie target (days on budget, weekend days over, rest
days under) also need the same goal and a target within ``_BUDGET_DRIFT``,
so a new target never reads as eating differently. Snapshots stored before
meal types were kept can't tell thin days, so what needs them doesn't
compare.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.domain.model.weekly_recap import LastWeek
from src.domain.services.weekly_recap.context import RecapContext

# Float noise must not hide a change of exactly one step (2.2 L - 2.0 L).
_EPSILON = 1e-9
_WEEKEND = (4, 5, 6)
# Meal types a day needs before it looks fully logged.
_FULL_DAY_MEAL_TYPES = 2
# How far the calorie target may move before counts against it don't compare.
_BUDGET_DRIFT = 0.05


@dataclass(frozen=True)
class Metric:
    """One number a card shows, read from this week and from last week.

    ``shown`` formats a value the way the card writes it, and ``step`` is
    the smallest change worth pointing out. ``count`` marks whole-number
    metrics. ``covered`` reads how many days fed the number this week and
    last week; the weeks compare only when the two agree and are known.
    ``budget_relative`` marks counts against the calorie target.
    """

    now: Callable[[RecapContext], float | None]
    last: Callable[[LastWeek], float | None]
    shown: Callable[[RecapContext, float], str]
    step: float
    higher_is_better: bool = True
    count: bool = False
    covered: tuple[Callable[[RecapContext], Any], Callable[[LastWeek], Any]] | None = (
        None
    )
    budget_relative: bool = False


def counted_days(ctx: RecapContext) -> list[int]:
    """Days with food logged that are not cheat days."""
    cheat = set(ctx.stats.cheat_days)
    return [day for day in range(7) if ctx.facts.logged[day] and day not in cheat]


def _full(ctx: RecapContext, day: int) -> bool:
    """Whether ``day`` looks fully logged (see the module docstring)."""
    target = ctx.facts.targets[day] * ctx.config.full_day_ratio
    return (
        ctx.stats.meal_types_by_day[day] >= _FULL_DAY_MEAL_TYPES
        and ctx.stats.kcal_by_day[day] >= target
    )


def _types_known(ctx: RecapContext) -> bool:
    return len(ctx.stats.meal_types_by_day) == 7


def thin_days(ctx: RecapContext) -> int | None:
    """Logged days that don't look fully logged; None when it can't be told."""
    if not _types_known(ctx):
        return None
    return sum(1 for day in range(7) if ctx.facts.logged[day] and not _full(ctx, day))


def weekend_days_counted(ctx: RecapContext) -> int | None:
    """Friday to Sunday days whose over-or-not the logs can tell.

    A day that went over did, however little was logged; a day under target
    has to be fully logged before it says the weekend went well.
    """
    if not _types_known(ctx):
        return None
    over = set(ctx.facts.weekend_over_days)
    return sum(
        1
        for day in counted_days(ctx)
        if day in _WEEKEND and (day in over or _full(ctx, day))
    )


def rest_days_counted(ctx: RecapContext) -> int:
    """Counted days without a workout, which the rest-day count looks at.

    A sparsely logged rest day can only look under-eaten, never fixed, so
    every counted day counts.
    """
    workout_days = set(ctx.facts.workout_days)
    return sum(1 for day in counted_days(ctx) if day not in workout_days)


def water_days(ctx: RecapContext) -> int:
    """Days with any drink logged."""
    return sum(1 for litres in ctx.stats.water_by_day if litres > 0)


def timed_days(ctx: RecapContext) -> int:
    """Days logged live, whose meals say when they were eaten.

    A day with a meal logged later (stamped at noon) could hide that
    night's snack. Weeks stored before this was kept count every day.
    """
    backdated = ctx.stats.backdated_days
    if backdated is None:
        return ctx.facts.days_logged
    return sum(1 for day in range(7) if ctx.facts.logged[day] and day not in backdated)


def _grams(ctx: RecapContext, value: float) -> str:
    return f"{ctx.num(value)}g"


def _litres(ctx: RecapContext, value: float) -> str:
    return ctx.t("card.value.litres", v=ctx.dec(value))


def _whole(ctx: RecapContext, value: float) -> str:
    return ctx.num(value)


def _counted(key: str) -> Callable[[RecapContext, float], str]:
    """A count with its unit, for cards whose headline is another number."""

    def shown(ctx: RecapContext, value: float) -> str:
        return ctx.t(key, count=int(value), n=ctx.num(value))

    return shown


def _positive(value: float | None) -> float | None:
    """An average, or None when nothing was logged to average."""
    return value if value is not None and value > 0 else None


def _water_now(ctx: RecapContext) -> float | None:
    return ctx.facts.water_avg if ctx.facts.water_tracked else None


def _logged_and_thin(days_logged: int, thin: int | None) -> tuple[int, int] | None:
    return None if thin is None else (days_logged, thin)


_THIN_DAYS = (thin_days, lambda last: last.thin_days)

PROTEIN = Metric(
    lambda ctx: _positive(ctx.facts.protein_avg),
    lambda last: _positive(last.protein_avg),
    _grams,
    step=5,
    covered=_THIN_DAYS,
)
FIBER = Metric(
    lambda ctx: _positive(ctx.stats.fiber_avg),
    lambda last: _positive(last.fiber_avg),
    _grams,
    step=2,
    covered=_THIN_DAYS,
)
# The water average counts days with no drink as zero, so the two weeks
# need the same number of such days.
WATER = Metric(
    _water_now,
    lambda last: last.water_avg,
    _litres,
    step=0.2,
    covered=(
        lambda ctx: ctx.facts.week_days - water_days(ctx),
        lambda last: last.week_days - last.water_days,
    ),
)
ON_BUDGET = Metric(
    lambda ctx: ctx.facts.days_on_budget,
    lambda last: last.days_on_budget,
    _counted("trend.value.days"),
    step=1,
    count=True,
    covered=(
        lambda ctx: _logged_and_thin(ctx.facts.days_logged, thin_days(ctx)),
        lambda last: _logged_and_thin(last.days_logged, last.thin_days),
    ),
    budget_relative=True,
)
WORKOUTS = Metric(
    lambda ctx: len(ctx.stats.workouts),
    lambda last: last.workouts,
    _counted("trend.value.workouts"),
    step=1,
    count=True,
    covered=(lambda ctx: ctx.facts.week_days, lambda last: last.week_days),
)
LATE_SNACKS = Metric(
    lambda ctx: len(ctx.stats.late_snack_days),
    lambda last: last.late_snack_nights,
    _whole,
    step=1,
    higher_is_better=False,
    count=True,
    covered=(
        timed_days,
        lambda last: last.days_logged if last.timed_days is None else last.timed_days,
    ),
)
SUGARY_DRINKS = Metric(
    lambda ctx: ctx.stats.sugary_drinks,
    lambda last: last.sugary_drinks,
    _whole,
    step=1,
    higher_is_better=False,
    count=True,
    covered=(water_days, lambda last: last.water_days),
)
WEEKEND_OVER = Metric(
    lambda ctx: len(ctx.facts.weekend_over_days),
    lambda last: last.weekend_over_days,
    _whole,
    step=1,
    higher_is_better=False,
    count=True,
    covered=(weekend_days_counted, lambda last: last.weekend_days_counted),
    budget_relative=True,
)
REST_UNDER = Metric(
    lambda ctx: len(ctx.facts.rest_under_days),
    lambda last: last.rest_under_days,
    _whole,
    step=1,
    higher_is_better=False,
    count=True,
    covered=(rest_days_counted, lambda last: last.rest_days_counted),
    budget_relative=True,
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
    if metric.budget_relative and not _same_target(ctx, last):
        return None
    if metric.covered is not None:
        covered_now, covered_last = metric.covered
        days_now, days_last = covered_now(ctx), covered_last(last)
        if days_now is None or days_last is None or days_now != days_last:
            return None
    now, before = metric.now(ctx), metric.last(last)
    if now is None or before is None:
        return None
    return float(now), float(before)


def _same_target(ctx: RecapContext, last: LastWeek) -> bool:
    """Whether last week had this week's goal and about its calorie target."""
    stats = ctx.stats
    if last.goal != stats.goal or not last.budget:
        return False
    return abs(stats.budget - last.budget) <= _BUDGET_DRIFT * last.budget


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
