"""The closing slides: next week's plan and the share card."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from src.domain.services.weekly_recap.cases import CaseParams
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.format import js_round
from src.domain.services.weekly_recap.habits import CHALLENGES, HABITS
from src.domain.services.weekly_recap.insights_rhythm import meals_stat, streak_stat
from src.domain.services.weekly_recap.types import Insight, Issue, Move

_SHARE_TITLES = frozenset({"first_week", "comeback", "perfect", "bulk"})
_SHARE_STATS = 3
_MINUS = "−"


def plan_moves(
    ctx: RecapContext, params: CaseParams, grow_issues: Sequence[Issue]
) -> list[Move]:
    """A challenge, habits, or the shown issues' advice topped up with habits.

    Habits only top up kinds the plan doesn't already cover.
    """
    if params.challenge:
        moves = [next(item.advice(ctx) for item in CHALLENGES if item.cond(ctx))]
    elif params.habits:
        moves = [habit.advice(ctx) for habit in HABITS if habit.cond(ctx)]
    else:
        moves = [issue.advice(ctx) for issue in grow_issues]
        for habit in HABITS:
            if len(moves) >= params.advice:
                break
            if not habit.cond(ctx):
                continue
            move = habit.advice(ctx)
            if all(existing.kind != move.kind for existing in moves):
                moves.append(move)
    moves = moves[: params.advice]
    if not moves and params.advice > 0:
        moves = [CHALLENGES[-1].advice(ctx)]
    return moves


def plan(ctx: RecapContext, case_id: str, moves: Sequence[Move]) -> dict[str, Any]:
    """Plan slide."""
    return {
        "title": _plan_title(ctx, case_id, len(moves)),
        "moves": [move.to_payload() for move in moves],
    }


def share(
    ctx: RecapContext,
    case_id: str,
    shown: Sequence[Insight],
    moves: Sequence[Move],
) -> dict[str, Any]:
    """Share card: three stat tiles, goal or streak progress, next week's plan."""
    if case_id in _SHARE_TITLES:
        title = ctx.t(f"share.title.{case_id}")
    else:
        title = ctx.t("share.title.default")
    shorts = [move.short for move in moves[:2]]
    return {
        "title": title,
        "stats": _share_stats(ctx, shown),
        "progress": _progress(ctx, case_id),
        "foot": ctx.t("share.foot", items=ctx.names(shorts)) if shorts else None,
    }


def _plan_title(ctx: RecapContext, case_id: str, count: int) -> str:
    if case_id == "perfect":
        return ctx.t("plan.title.challenge")
    if case_id == "comeback" or count == 0:
        return ctx.t("plan.title.keep")
    if count >= 3:
        return ctx.t("plan.title.many", n=count)
    return ctx.t("plan.title.2" if count == 2 else "plan.title.1")


def _days_logged_stat(ctx: RecapContext) -> list[str]:
    facts = ctx.facts
    return [f"{facts.days_logged}/{facts.week_days}", ctx.t("stat.days_logged")]


def _share_stats(ctx: RecapContext, shown: Sequence[Insight]) -> list[list[str]]:
    """The shown cards' tiles, topped up with logging tiles, no repeats."""
    tiles = [insight.stat(ctx) for insight in shown if insight.stat is not None]
    fillers: list[Callable[[RecapContext], list[str]]] = [
        meals_stat,
        _days_logged_stat,
    ]
    if ctx.stats.streak > 0:
        fillers.append(streak_stat)
    for filler in fillers:
        if len(tiles) >= _SHARE_STATS:
            break
        tile = filler(ctx)
        if all(existing[1] != tile[1] for existing in tiles):
            tiles.append(tile)
    return tiles[:_SHARE_STATS]


def _progress(ctx: RecapContext, case_id: str) -> dict[str, Any] | None:
    """Goal progress when there is a target weight, else the streak, if any."""
    stats, config = ctx.stats, ctx.config
    if stats.progress_pct is not None:
        bulk = stats.goal == "bulk"
        if case_id == "first_week":
            sub = ctx.t(
                "share.progress.week", n=stats.week_no, total=config.progress_weeks
            )
        else:
            sub = _delta_sub(ctx, stats.progress_delta)
        return {
            "label": ctx.t("share.progress.bulk" if bulk else "share.progress.cut"),
            "pct": round(min(1.0, max(0.0, stats.progress_pct)), 2),
            "sub": sub,
        }
    if stats.streak <= 0:
        return None
    days = config.streak_progress_days
    pct = min(1.0, stats.streak / days) if days > 0 else 0.0
    return {
        "label": ctx.t("share.progress.streak"),
        "pct": round(pct, 2),
        "sub": ctx.t("share.progress.streak_days", count=stats.streak, n=stats.streak),
    }


def _delta_sub(ctx: RecapContext, delta: float | None) -> str:
    points = js_round((delta or 0.0) * 100)
    if points == 0:
        return ctx.t("share.progress.same")
    sign = "+" if points > 0 else _MINUS
    return ctx.t("share.progress.delta", delta=f"{sign}{abs(points)}")
