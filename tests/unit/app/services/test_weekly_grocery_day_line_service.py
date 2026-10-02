"""Day grocery notes replace one ingredient without touching pantry stock."""

from types import SimpleNamespace

import pytest

from src.api.exceptions import ResourceNotFoundException, ValidationException
from src.app.commands.meal_planner import UpdateGroceryDayLinesCommand
from src.app.services.weekly_grocery_day_line_service import (
    WeeklyGroceryDayLineService,
)


class _Reservation:
    state = "reserved"


class _Operations:
    async def reserve(self, **kwargs):
        return _Reservation()

    async def complete(self, reservation, **kwargs):
        return None

    async def release(self, reservation):
        return None


class _Plans:
    def __init__(self, plan=SimpleNamespace(id="plan-1")):
        self.plan = plan
        self.calls = []

    async def replace_grocery_day_lines(self, **kwargs):
        self.calls.append(kwargs)
        return self.plan


class _Uow:
    def __init__(self, plans):
        self.meal_write_operations = _Operations()
        self.weekly_meal_plans = plans

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _command(**overrides):
    payload = {
        "user_id": "user-1",
        "plan_id": "plan-1",
        "idempotency_key": "key-1",
        "ingredient_id": 42,
        "lines": [{"day_index": 4, "needed_amount": 200, "covered": True}],
    }
    payload.update(overrides)
    return UpdateGroceryDayLinesCommand(**payload)


@pytest.mark.asyncio
async def test_day_note_replaces_one_ingredient_and_leaves_pantry_alone():
    plans = _Plans()
    service = WeeklyGroceryDayLineService(lambda: _Uow(plans))

    result = await service.update(_command())

    assert result == {"success": True, "updated_count": 1}
    assert plans.calls == [
        {
            "user_id": "user-1",
            "plan_id": "plan-1",
            "ingredient_id": 42,
            "lines": [{"day_index": 4, "needed_amount": 200.0, "covered": True}],
        }
    ]


@pytest.mark.asyncio
async def test_empty_day_line_is_rejected():
    service = WeeklyGroceryDayLineService(lambda: _Uow(_Plans()))

    with pytest.raises(ValidationException):
        await service.update(
            _command(lines=[{"day_index": 1, "needed_amount": None, "covered": False}])
        )


@pytest.mark.asyncio
async def test_missing_plan_is_not_found():
    service = WeeklyGroceryDayLineService(lambda: _Uow(_Plans(plan=None)))

    with pytest.raises(ResourceNotFoundException):
        await service.update(_command(lines=[]))
