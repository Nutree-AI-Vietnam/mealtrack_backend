"""The story slides: intro, wins and "room to improve"."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.types import Card, Issue

_OWN_EYEBROW = frozenset({"first_week", "comeback"})
_OWN_WINS_EYEBROW = frozenset({"low_data", "first_week", "comeback", "tough"})
_FIXED_WINS_TITLE = frozenset({"first_week", "comeback", "perfect"})
_GROW_PREFIX = {"tough": "tough.grow", "bulk": "bulk.grow"}
_MIN_COMEBACK_STREAK = 2
# Full weeks whose subtitle can name the dish the user kept coming back to.
_DISH_SUB = frozenset({"steady", "bulk", "perfect"})
_GO_TO_DISH_MIN = 3


def intro(ctx: RecapContext, case_id: str) -> dict[str, Any]:
    """Opening slide: the week's headline and its meals-by-day grid."""
    stats, facts = ctx.stats, ctx.facts
    if case_id in _OWN_EYEBROW:
        eyebrow = ctx.t(f"{case_id}.eyebrow")
    else:
        eyebrow = ctx.t("intro.eyebrow.week", n=stats.week_no)
    if case_id == "comeback":
        big = facts.days_logged
        big_unit = ctx.t("intro.big_unit.days", n=facts.week_days)
    else:
        big = facts.meals_logged
        big_unit = ctx.t("intro.big_unit.meals", n=facts.meals_expected)
    return {
        "eyebrow": eyebrow,
        "title": _title(ctx, case_id),
        "sub": _sub(ctx, case_id),
        "label": ctx.t(f"{case_id}.label"),
        "big": big,
        "big_unit": big_unit,
        "meals_by_day": list(stats.meals_by_day),
        "chip": _chip(ctx, case_id),
    }


def wins(
    ctx: RecapContext, case_id: str, cards: Sequence[Card], grow_limit: int
) -> dict[str, Any] | None:
    """Win cards (or, for new and quiet loggers, what Nutree learned)."""
    if not cards:
        return None
    if case_id in _OWN_WINS_EYEBROW:
        eyebrow = ctx.t(f"{case_id}.wins.eyebrow")
    else:
        eyebrow = ctx.t("wins.eyebrow")
    return {
        "eyebrow": eyebrow,
        "title": _wins_title(ctx, case_id, len(cards)),
        "foot": _wins_foot(ctx, grow_limit),
        "cards": list(cards),
    }


def grow(
    ctx: RecapContext, case_id: str, issues: Sequence[Issue]
) -> dict[str, Any] | None:
    """Cards for the issues the plan addresses."""
    if not issues:
        return None
    prefix = _GROW_PREFIX.get(case_id, "grow")
    shown = len(issues)
    if shown == 1:
        title = ctx.t(f"{prefix}.title_one")
    else:
        title = ctx.t(f"{prefix}.title_n", n=shown)
    return {
        "eyebrow": ctx.t(f"{prefix}.eyebrow"),
        "title": title,
        "foot": None,
        "cards": [issue.card(ctx) for issue in issues],
    }


def _title(ctx: RecapContext, case_id: str) -> str:
    """The headline, speaking to the user by name when we know it."""
    name = ctx.stats.first_name
    if name:
        return ctx.t(f"{case_id}.title.named", name=name)
    return ctx.t(f"{case_id}.title")


def _sub(ctx: RecapContext, case_id: str) -> str:
    """The case's line, or for a full week, the dish it kept coming back to."""
    dishes = ctx.stats.top_dishes
    if case_id in _DISH_SUB and dishes and dishes[0].count >= _GO_TO_DISH_MIN:
        top = dishes[0]
        return ctx.t(f"{case_id}.sub.dish", dish=top.name, n=top.count)
    return ctx.t(f"{case_id}.sub")


def _chip(ctx: RecapContext, case_id: str) -> dict[str, str] | None:
    picked = _case_chip(ctx, case_id)
    if picked is None:
        if case_id == "comeback":
            return None
        facts = ctx.facts
        picked = (
            "edit_note",
            ctx.t("chip.days_logged", n=facts.days_logged, d=facts.week_days),
        )
    icon, text = picked
    return {"icon": icon, "text": text}


def _case_chip(ctx: RecapContext, case_id: str) -> tuple[str, str] | None:
    stats, facts = ctx.stats, ctx.facts
    if case_id == "low_data":
        return "info", ctx.t("low_data.chip", n=facts.low_data_threshold)
    if case_id == "first_week":
        return "emoji_events", ctx.t("first_week.chip")
    if case_id == "comeback" and stats.streak >= _MIN_COMEBACK_STREAK:
        return "local_fire_department", ctx.t("comeback.chip", n=stats.streak)
    if case_id == "tough":
        return "favorite", ctx.t("tough.chip")
    if case_id == "perfect":
        before = stats.perfect_weeks_before
        if before == 0:
            return "workspace_premium", ctx.t("perfect.chip.first")
        return "workspace_premium", ctx.t("perfect.chip.nth", n=before + 1)
    if case_id == "bulk" and facts.strength_count > 0:
        key = "bulk.chip.record" if stats.workouts_is_record else "bulk.chip"
        sessions = facts.strength_count
        return "fitness_center", ctx.t(key, count=sessions, n=sessions)
    if case_id == "steady" and stats.streak >= ctx.config.streak_goal:
        key = (
            "steady.chip.streak_record"
            if stats.streak_is_record
            else ("steady.chip.streak")
        )
        return "local_fire_department", ctx.t(key, n=stats.streak)
    return None


def _wins_title(ctx: RecapContext, case_id: str, shown: int) -> str:
    if case_id == "low_data":
        meals = ctx.facts.meals_logged
        return ctx.t("low_data.wins.title", count=meals, n=meals)
    if case_id in _FIXED_WINS_TITLE:
        return ctx.t(f"{case_id}.wins.title")
    key = "tough.wins.title" if case_id == "tough" else "wins.title"
    return ctx.t(key, count=shown, n=shown)


def _wins_foot(ctx: RecapContext, grow_limit: int) -> str | None:
    """A single over-budget day that the next day made up for."""
    facts = ctx.facts
    if len(facts.over_days) != 1 or grow_limit <= 0:
        return None
    day = facts.over_days[0]
    after = day + 1
    if after > 6 or not facts.logged[after]:
        return None
    if ctx.stats.kcal_by_day[after] >= facts.targets[after]:
        return None
    trained = day in facts.workout_days or after in facts.workout_days
    key = "wins.foot.workout" if trained else "wins.foot.plain"
    return ctx.t(key, day=ctx.day(day), kcal=ctx.num(facts.over_kcal))
