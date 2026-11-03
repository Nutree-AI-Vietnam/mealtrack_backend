"""A plan tells the user how much to eat, and says so in its API response."""

import pytest

from src.api.routes.v1.meal_plans import _slot_response
from src.domain.model.weekly_meal_planner import (
    MAX_PLANNED_RICE_STEPS,
    RICE_STEP_GRAMS,
    WeeklyMealPlanSlot,
)


def _slot(**overrides):
    base = {
        "id": "slot-1",
        "day_index": 0,
        "slot_index": 2,
        "recipe_id": "com-tam",
    }
    base.update(overrides)
    return WeeklyMealPlanSlot(**base)


def test_a_plan_written_before_composing_reads_as_one_plain_serving():
    slot = _slot()

    assert (slot.planned_portion, slot.planned_rice_steps) == (1.0, 0)


@pytest.mark.parametrize("portion", [0.5, 1.0, 1.5, 2.0])
def test_every_portion_the_log_endpoint_accepts_is_allowed(portion):
    assert _slot(planned_portion=portion).planned_portion == portion


@pytest.mark.parametrize("portion", [0.75, 1.25, 0.0, 3.0])
def test_a_portion_the_user_could_not_log_is_refused(portion):
    with pytest.raises(ValueError):
        _slot(planned_portion=portion)


@pytest.mark.parametrize("steps", [-1, MAX_PLANNED_RICE_STEPS + 1])
def test_rice_beyond_two_bowls_is_refused(steps):
    with pytest.raises(ValueError):
        _slot(planned_rice_steps=steps)


def test_the_response_carries_the_serving_and_the_rice_in_grams():
    response = _slot_response(
        _slot(planned_portion=1.5, planned_rice_steps=3), meal=None
    )

    assert response.planned_portion == 1.5
    assert response.planned_rice_steps == 3
    assert response.planned_rice_grams == 3 * RICE_STEP_GRAMS == 165
