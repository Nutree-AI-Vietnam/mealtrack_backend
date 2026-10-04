from datetime import date

import pytest

from src.domain.model.weekly_meal_planner import (
    WEEKLY_PLAN_SLOT_COUNT,
    WEEKLY_SLOTS_PER_DAY,
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)


def _slots():
    return tuple(
        WeeklyMealPlanSlot(
            id=f"slot-{day}-{slot}",
            day_index=day,
            slot_index=slot,
            recipe_id=None,
        )
        for day in range(7)
        for slot in range(WEEKLY_SLOTS_PER_DAY)
    )


def test_weekly_plan_requires_monday_and_exactly_twenty_one_slots():
    with pytest.raises(ValueError, match="Monday"):
        WeeklyMealPlan(
            id="plan",
            user_id="user",
            week_start_date=date(2026, 9, 22),
            status=WeeklyMealPlanStatus.DRAFT,
            people=1,
            preferences=WeeklyMealPlanPreferences(),
            timezone="UTC",
            slots=_slots(),
        )

    with pytest.raises(ValueError, match=f"exactly {WEEKLY_PLAN_SLOT_COUNT}"):
        WeeklyMealPlan(
            id="plan",
            user_id="user",
            week_start_date=date(2026, 9, 21),
            status=WeeklyMealPlanStatus.DRAFT,
            people=1,
            preferences=WeeklyMealPlanPreferences(),
            timezone="UTC",
            slots=_slots()[:-1],
        )
