"""The seven recap stories, and which one a week gets.

A case sets how many "room to improve" cards and plan items the story
shows, whether it ends on a share card, and whether it shows what Nutree
learned (new or quiet loggers) or a challenge (perfect weeks) instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from src.domain.services.weekly_recap.config import RecapConfig
from src.domain.services.weekly_recap.context import RecapContext
from src.domain.services.weekly_recap.types import Issue


@dataclass(frozen=True)
class CaseParams:
    """How one story is laid out."""

    grow: int
    advice: int
    share: bool
    habits: bool = False
    challenge: bool = False


CASES: dict[str, CaseParams] = {
    "low_data": CaseParams(grow=0, advice=1, share=False, habits=True),
    "first_week": CaseParams(grow=0, advice=3, share=True, habits=True),
    "comeback": CaseParams(grow=0, advice=2, share=True),
    "tough": CaseParams(grow=2, advice=2, share=False),
    "perfect": CaseParams(grow=0, advice=1, share=True, challenge=True),
    "bulk": CaseParams(grow=2, advice=3, share=True),
    "steady": CaseParams(grow=3, advice=3, share=True),
}


def case_params(case_id: str, config: RecapConfig) -> CaseParams:
    """The case's layout, with any ``cases`` override from config applied."""
    params = CASES[case_id]
    return replace(
        params,
        grow=config.case_limit(case_id, "grow", params.grow),
        advice=config.case_limit(case_id, "advice", params.advice),
    )


def pick_case(ctx: RecapContext, issues: Sequence[Issue]) -> str:
    """The first case whose rule matches, in the design engine's order."""
    stats, facts, config = ctx.stats, ctx.facts, ctx.config
    if facts.low_data:
        return "low_data"
    new_account = stats.account_days < config.first_week_account_days
    if new_account and not stats.had_first_week:
        return "first_week"
    if (
        stats.gap_before >= config.comeback_gap_days
        and facts.days_logged >= config.comeback_min_days
    ):
        return "comeback"
    if _tough(ctx):
        return "tough"
    if not issues and _perfect(ctx):
        return "perfect"
    if stats.goal == "bulk":
        return "bulk"
    return "steady"


def _tough(ctx: RecapContext) -> bool:
    config = ctx.config
    if ctx.stats.goal == "bulk":
        return len(ctx.stats.under_days) >= config.tough_bulk_under_min
    return ctx.facts.days_on_budget <= config.tough_on_budget_max


def _perfect(ctx: RecapContext) -> bool:
    """No issues is not enough: the week also has to hit its targets."""
    facts, config = ctx.facts, ctx.config
    return (
        facts.water_tracked
        and facts.days_on_budget >= config.on_budget_days
        and facts.protein_days_hit >= config.protein_hit_days
    )
