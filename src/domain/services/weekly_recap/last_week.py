"""Last week, as this week's recap sees it: its numbers, its plan, and the
win for sticking to that plan.

``last_week_of`` summarizes a stored week for the week after it. Its plan
is the one that week's recap showed: the issue ids stored with it, or for
recaps stored before those were kept, the rebuilt plan checked against the
stored moves (``shown_plan``).

The follow-through card is shown when an issue last week's plan took on no
longer holds this week and its number moved at least a step the right way,
over the same days logged (see ``compare``). The first such issue, in last
week's plan order, gets the card.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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
    rest_days_counted,
    timed_days,
    trend,
    water_days,
    weekend_days_counted,
)
from src.domain.services.weekly_recap.config import RecapConfig
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.facts import week_facts
from src.domain.services.weekly_recap.issues_food import FOOD_ISSUES
from src.domain.services.weekly_recap.issues_load import LOAD_ISSUES
from src.domain.services.weekly_recap.strings import TABLES
from src.domain.services.weekly_recap.types import Card, Insight, Issue, ranked
from src.domain.services.weekly_recap.viz import card

_ALL_ISSUES = (*FOOD_ISSUES, *LOAD_ISSUES)
_ISSUES: dict[str, Issue] = {issue.id: issue for issue in _ALL_ISSUES}
_MOVE_KEYS = ("kind", "icon", "title")


def planned_issue_ids(
    stats: WeekStats, case_id: str, config: RecapConfig
) -> tuple[str, ...]:
    """The issues whose advice the plan of ``stats`` shows, in plan order.

    Stories that plan habits or a challenge instead take on none.
    """
    if case_id not in CASES:
        return ()
    params = case_params(case_id, config)
    if params.habits or params.challenge:
        return ()
    ctx = RecapContext(stats, week_facts(stats, config), config, "en")
    issues = ranked([issue for issue in _ALL_ISSUES if issue.cond(ctx)], config)
    return tuple(issue.id for issue in issues[: params.grow][: params.advice])


def shown_plan(
    stats: WeekStats,
    case_id: str,
    config: RecapConfig,
    moves: Sequence[Any],
    locale: str,
) -> tuple[str, ...]:
    """The rebuilt plan, kept only where it matches the moves that were shown.

    For recaps stored before their plan's issue ids were. An issue stays
    only when its advice, told in the stored ``locale``, reads exactly like
    the stored move at the same place, so a config or code change since
    then never credits advice the user did not see.
    """
    told = locale if locale in TABLES else "en"
    ctx = RecapContext(stats, week_facts(stats, config), config, told)
    kept: list[str] = []
    for index, issue_id in enumerate(planned_issue_ids(stats, case_id, config)):
        move = moves[index] if index < len(moves) else None
        advice = _ISSUES[issue_id].advice(ctx).to_payload()
        if isinstance(move, Mapping) and all(
            move.get(key) == advice[key] for key in _MOVE_KEYS
        ):
            kept.append(issue_id)
    return tuple(kept)


def last_week_of(
    stats: WeekStats,
    case_id: str,
    config: RecapConfig,
    plan: Sequence[str] | None = None,
    plan_moves: int | None = None,
    plan_exact: bool = True,
) -> LastWeek:
    """Summarize a stored week told as ``case_id`` for the week after it.

    ``plan`` is the issue ids its plan showed (rebuilt from the numbers
    when not given), ``plan_moves`` how many moves it showed in all, and
    ``plan_exact`` False when the ids were rebuilt for an older row.
    """
    facts = week_facts(stats, config)
    ctx = RecapContext(stats, facts, config, "en")
    if plan is None:
        plan = planned_issue_ids(stats, case_id, config)
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
        days_logged=facts.days_logged,
        water_days=water_days(ctx),
        weekend_days_counted=weekend_days_counted(ctx),
        rest_days_counted=rest_days_counted(ctx),
        pre_workout_carbs=stats.pre_workout_carbs,
        plan=tuple(plan),
        timed_days=timed_days(ctx),
        plan_moves=plan_moves,
        plan_exact=plan_exact,
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


def _fixed(ctx: RecapContext, issue_id: str) -> tuple[float, float] | None:
    """This week's and last week's number, if the planned issue was fixed."""
    issue, tracked = _ISSUES.get(issue_id), _TRACKED.get(issue_id)
    if issue is None or tracked is None or issue.cond(ctx):
        return None
    pair = compared(ctx, tracked[1])
    return pair if pair is not None and improved(tracked[1], *pair) else None


def _followed(ctx: RecapContext) -> tuple[str, float] | None:
    """The first planned issue fixed this week, with this week's number."""
    last = ctx.stats.last_week
    if last is None:
        return None
    for issue_id in last.plan:
        pair = _fixed(ctx, issue_id)
        if pair is not None:
            return issue_id, pair[0]
    return None


def _unit(ctx: RecapContext, issue_id: str, metric: Metric, now: float) -> str:
    count = int(now) if metric.count else None
    return ctx.t(f"follow.unit.{issue_id}", count=count)


def _follow_card(ctx: RecapContext) -> Card:
    followed = _followed(ctx)
    if followed is None:
        raise ValueError("no plan item was followed through")
    issue_id, now = followed
    kind, metric = _TRACKED[issue_id]
    # This week's number, with last week's in the trend line beside it. The
    # title claims the whole plan only when every item of it was fixed.
    return card(
        kind,
        "check_circle",
        ctx.t("follow.title" if _plan_done(ctx) else "follow.title.item"),
        value=metric.shown(ctx, now),
        unit=_unit(ctx, issue_id, metric, now),
        note=ctx.t("follow.note", goal=ctx.t(f"{issue_id}.short")),
        trend=trend(ctx, metric),
    )


def _plan_done(ctx: RecapContext) -> bool:
    """Whether every move of last week's plan was an issue now fixed.

    A plan with a habit (a reminder) or one rebuilt for an older row can't
    be checked whole, so it never claims to be done.
    """
    last = ctx.stats.last_week
    if last is None or not last.plan_exact:
        return False
    if last.plan_moves is not None and last.plan_moves != len(last.plan):
        return False
    return all(_fixed(ctx, planned) is not None for planned in last.plan)


def _follow_stat(ctx: RecapContext) -> list[str] | None:
    """Share tile for a fix that reads as a win on its own (more of something).

    A bare count of what there was less of ("1 late-snack night") needs the
    comparison beside it, which a share tile has no room for.
    """
    followed = _followed(ctx)
    if followed is None:
        return None
    issue_id, now = followed
    _, metric = _TRACKED[issue_id]
    if not metric.higher_is_better:
        return None
    return [metric.shown(ctx, now), _unit(ctx, issue_id, metric, now)]


FOLLOW_THROUGH = Insight(
    "follow_through",
    95,
    lambda ctx: _followed(ctx) is not None,
    _follow_card,
    _follow_stat,
)
