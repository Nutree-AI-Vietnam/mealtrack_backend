"""In-memory repair of a lunch/dinner weekly plan."""

from datetime import date

import pytest

from src.domain.model.weekly_meal_planner import WeeklyMealPlanStatus
from src.infra.database.models.weekly_meal_planner import (
    WeeklyMealPlanORM,
    WeeklyMealPlanSlotORM,
)
from src.infra.repositories.weekly_meal_plan_repository_async import (
    AsyncWeeklyMealPlanRepository,
    _to_domain,
)


class _Session:
    def __init__(self):
        self.flushes = 0

    async def flush(self):
        self.flushes += 1


def _legacy_plan():
    plan = WeeklyMealPlanORM(
        id="plan",
        user_id="user",
        week_start_date=date(2026, 10, 5),
        status="draft",
        people=1,
        preferences={},
        timezone="UTC",
        algorithm_version="v1",
        revision=4,
    )
    plan.slots = [
        WeeklyMealPlanSlotORM(
            id=f"{day}-{slot}",
            plan_id="plan",
            day_index=day,
            slot_index=slot,
            catalog_meal_id=f"recipe-{slot}",
            is_logged=day == 1 and slot == 1,
            logged_meal_id="logged-meal" if day == 1 and slot == 1 else None,
            version=3 if day == 1 and slot == 1 else 1,
        )
        for day in range(7)
        for slot in (0, 1)
    ]
    return plan


@pytest.mark.asyncio
async def test_repair_moves_lunch_and_dinner_and_can_be_read():
    plan = _legacy_plan()
    session = _Session()
    repository = AsyncWeeklyMealPlanRepository(session)  # type: ignore[arg-type]

    assert await repository._repair_late_legacy_slots(plan) is True
    assert await repository._repair_late_legacy_slots(plan) is False
    assert session.flushes == 3

    by_id = {slot.id: slot for slot in plan.slots}
    assert by_id["0-0"].slot_index == 1
    assert by_id["0-0"].catalog_meal_id == "recipe-0"
    assert by_id["1-1"].slot_index == 2
    assert by_id["1-1"].is_logged is True
    assert by_id["1-1"].logged_meal_id == "logged-meal"
    assert by_id["1-1"].version == 3
    breakfasts = [slot for slot in plan.slots if slot.slot_index == 0]
    assert len(breakfasts) == 7
    assert all(
        slot.catalog_meal_id is None and not slot.is_logged for slot in breakfasts
    )

    loaded = _to_domain(plan)
    assert len(loaded.slots) == 21
    assert loaded.status is WeeklyMealPlanStatus.DRAFT
    assert loaded.revision == 4


@pytest.mark.asyncio
async def test_repair_skips_a_three_slot_plan():
    plan = _legacy_plan()
    plan.algorithm_version = "v2"
    repository = AsyncWeeklyMealPlanRepository(_Session())  # type: ignore[arg-type]

    assert await repository._repair_late_legacy_slots(plan) is False
    assert {slot.slot_index for slot in plan.slots} == {0, 1}
