"""Repair weekly budget rows whose target_revision lags the profile."""

from __future__ import annotations

import logging
from typing import Any

from src.domain.model.weekly import WeeklyMacroBudget

logger = logging.getLogger(__name__)


def weekly_targets_from_daily_macros(daily_macros: dict[str, float]) -> dict[str, float]:
    """Scale canonical daily macro grams and derive weekly calories from them."""
    target_protein = round(daily_macros["protein"] * 7, 1)
    target_carbs = round(daily_macros["carbs"] * 7, 1)
    target_fat = round(daily_macros["fat"] * 7, 1)
    return {
        "target_calories": round(
            target_protein * 4 + target_carbs * 4 + target_fat * 9,
            1,
        ),
        "target_protein": target_protein,
        "target_carbs": target_carbs,
        "target_fat": target_fat,
    }


async def sync_weekly_budget_targets_if_stale(
    uow: Any,
    weekly_budget: WeeklyMacroBudget,
    *,
    daily_macros: dict[str, float] | None,
    profile_target_revision: int,
    persist: bool = True,
) -> WeeklyMacroBudget:
    """Rewrite stale weekly targets so Home can use the adjusted daily path."""
    if not daily_macros:
        return weekly_budget

    expected = weekly_targets_from_daily_macros(daily_macros)
    current = {
        "target_calories": weekly_budget.target_calories,
        "target_protein": weekly_budget.target_protein,
        "target_carbs": weekly_budget.target_carbs,
        "target_fat": weekly_budget.target_fat,
    }
    targets_changed = any(
        abs(expected[key] - current[key]) / max(abs(current[key]), 1) > 0.01
        for key in expected
    )
    if not targets_changed and weekly_budget.target_revision == profile_target_revision:
        return weekly_budget

    weekly_budget.target_calories = expected["target_calories"]
    weekly_budget.target_protein = expected["target_protein"]
    weekly_budget.target_carbs = expected["target_carbs"]
    weekly_budget.target_fat = expected["target_fat"]
    weekly_budget.target_revision = profile_target_revision
    if persist:
        await uow.weekly_budgets.update(weekly_budget)
    logger.info(
        "Updated stale weekly nutrition targets for user %s to revision %s",
        weekly_budget.user_id,
        profile_target_revision,
    )
    return weekly_budget
