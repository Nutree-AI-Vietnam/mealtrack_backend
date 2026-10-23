"""Last week, as this week's recap sees it: its numbers, its plan, and the
win for sticking to that plan.

``last_week_of`` summarizes a stored week for the week after it. The
follow-through card is shown when an issue last week's plan took on no
longer holds this week and its number moved at least a step the right way;
the first such issue, in last week's plan order, gets the card.
"""

from __future__ import annotations

from src.domain.model.weekly_recap import LastWeek, WeekStats
from src.domain.services.weekly_recap.cases import CASES, case_params
from src.domain.services.weekly_recap.compare import (
    FIBER,
    LATE_SNACKS,
    PRE_WORKOUT_CARBS,
    PROTEIN,
    REST_UNDER,
    SUGARY_DRINKS,
    WATER,
    WEEKEND_OVER,
    Metric,
    compared,
    improved,
    trend,
)
from src.domain.services.weekly_recap.config import RecapConfig
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.facts import week_facts
from src.domain.services.weekly_recap.issues_food import FOOD_ISSUES
from src.domain.services.weekly_recap.issues_load import LOAD_ISSUES
from src.domain.services.weekly_recap.types import Card, Insight, Issue, ranked
from src.domain.services.weekly_recap.viz import card

_ALL_ISSUES = (*FOOD_ISSUES, *LOAD_ISSUES)


def last_week_of(stats: WeekStats, case_id: str, config: RecapConfig) -> LastWeek:
    """Summarize a stored week told as ``case_id`` for the week after it.

    ``plan`` lists the issues whose advice that week's plan showed, in plan
    order. Stories that planned habits or a challenge instead have none.
    """
    facts = week_facts(stats, config)
    ctx = RecapContext(stats, facts, config, "en")
    plan: tuple[str, ...] = ()
    if case_id in CASES:
        params = case_params(case_id, config)
        if not (params.habits or params.challenge):
            issues = ranked([issue for issue in _ALL_ISSUES if issue.cond(ctx)], config)
            shown = issues[: params.grow][: params.advice]
            plan = tuple(issue.id for issue in shown)
    return LastWeek(
        week_days=facts.week_days,
        protein_avg=facts.protein_avg,
        fiber_avg=stats.fiber_avg,
        water_avg=facts.water_avg if facts.water_tracked else None,
        days_on_budget=facts.days_on_budget,
        workouts=len(stats.workouts),
        late_snack_nights=len(stats.late_snack_days),
        sugary_drinks=stats.sugary_drinks,
        weekend_over_days=len(facts.weekend_over_days),
        rest_under_days=len(facts.rest_under_days),
        pre_workout_carbs=stats.pre_workout_carbs,
        plan=plan,
    )


# Issue id: the card kind its own card uses, and the number that tracks it.
_TRACKED: dict[str, tuple[str, Metric]] = {
    "fiber_low": ("fiber", FIBER),
    "water_low": ("water", WATER),
    "late_snack": ("calm", LATE_SNACKS),
    "sugary": ("fat", SUGARY_DRINKS),
    "weekend_over": ("calories", WEEKEND_OVER),
    "protein_low": ("protein", PROTEIN),
    "rest_under": ("calories", REST_UNDER),
    "preworkout_carbs": ("carbs", PRE_WORKOUT_CARBS),
}
_ISSUES: dict[str, Issue] = {issue.id: issue for issue in _ALL_ISSUES}


def _followed(ctx: RecapContext) -> tuple[str, float, float] | None:
    """The first planned issue fixed this week, with its now and before."""
    last = ctx.stats.last_week
    if last is None:
        return None
    for issue_id in last.plan:
        issue, tracked = _ISSUES.get(issue_id), _TRACKED.get(issue_id)
        if issue is None or tracked is None or issue.cond(ctx):
            continue
        pair = compared(ctx, tracked[1])
        if pair is not None and improved(tracked[1], *pair):
            return issue_id, *pair
    return None


def _follow_card(ctx: RecapContext) -> Card:
    followed = _followed(ctx)
    if followed is None:
        raise ValueError("no plan item was followed through")
    issue_id, now, _ = followed
    kind, metric = _TRACKED[issue_id]
    # This week's number, with last week's in the trend line beside it.
    return card(
        kind,
        "check_circle",
        ctx.t("follow.title"),
        value=metric.shown(ctx, now),
        unit=ctx.t(f"follow.unit.{issue_id}", count=int(now) if metric.count else None),
        note=ctx.t("follow.note", goal=ctx.t(f"{issue_id}.short")),
        trend=trend(ctx, metric),
    )


FOLLOW_THROUGH = Insight(
    "follow_through",
    95,
    lambda ctx: _followed(ctx) is not None,
    _follow_card,
)
