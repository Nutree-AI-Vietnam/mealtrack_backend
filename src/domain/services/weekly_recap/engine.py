"""Weekly recap engine: one week's stats in, the app's story payload out.

A port of the Claude Design "Weekly Wrapped" engine (``generate`` in
wrap-engine.js). The app only renders the result, so every number and
string it shows is chosen and worded here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.domain.model.weekly_recap import WeekStats
from src.domain.services.weekly_recap.cases import case_params, pick_case
from src.domain.services.weekly_recap.config import RecapConfig
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.facts import week_facts
from src.domain.services.weekly_recap.format import format_range
from src.domain.services.weekly_recap.insights_good import GOOD
from src.domain.services.weekly_recap.insights_learn import LEARN
from src.domain.services.weekly_recap.issues_food import FOOD_ISSUES
from src.domain.services.weekly_recap.issues_load import LOAD_ISSUES
from src.domain.services.weekly_recap.sections_plan import plan, plan_moves, share
from src.domain.services.weekly_recap.sections_story import grow, intro, wins
from src.domain.services.weekly_recap.strings import TABLES
from src.domain.services.weekly_recap.types import Card, Insight, ranked

ISSUES = (*FOOD_ISSUES, *LOAD_ISSUES)
DEFAULT_LOCALE = "en"


def generate_recap(
    stats: WeekStats,
    config: RecapConfig | None = None,
    locale: str = DEFAULT_LOCALE,
) -> dict[str, Any]:
    """The recap payload for ``stats`` in ``locale`` (English if unsupported).

    Slides list only the sections that have content, in story order; the
    intro and plan slides are always there.
    """
    config = config or RecapConfig()
    if locale not in TABLES:
        locale = DEFAULT_LOCALE
    ctx = RecapContext(stats, week_facts(stats, config), config, locale)
    issues = ranked([issue for issue in ISSUES if issue.cond(ctx)], config)
    case_id = pick_case(ctx, issues)
    params = case_params(case_id, config)
    shown = _win_cards(ctx, LEARN if params.habits else GOOD)
    grow_issues = issues[: params.grow]
    moves = plan_moves(ctx, params, grow_issues)
    insights = [insight for insight, _ in shown]
    sections: dict[str, dict[str, Any] | None] = {
        "intro": intro(ctx, case_id),
        "wins": wins(ctx, case_id, [card for _, card in shown], params.grow),
        "grow": grow(ctx, case_id, grow_issues),
        "plan": plan(ctx, case_id, moves),
        "share": share(ctx, case_id, insights, moves) if params.share else None,
    }
    return {
        "case_id": case_id,
        "range": format_range(stats.week_start, locale),
        "week_start": stats.week_start.isoformat(),
        "slides": [name for name, section in sections.items() if section],
        **sections,
    }


def _win_cards(
    ctx: RecapContext, insights: Sequence[Insight]
) -> list[tuple[Insight, Card]]:
    """Top-priority wins that apply, at most one card per kind."""
    active = ranked([insight for insight in insights if insight.cond(ctx)], ctx.config)
    shown: list[tuple[Insight, Card]] = []
    for insight in active:
        if len(shown) >= ctx.config.max_cards:
            break
        card = insight.card(ctx)
        if all(existing["kind"] != card["kind"] for _, existing in shown):
            shown.append((insight, card))
    return shown
