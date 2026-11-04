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


def test_the_log_request_accepts_the_rice_the_plan_asked_for():
    from src.api.schemas.request.weekly_meal_planner_requests import (
        LogMealPlanSlotRequest,
    )

    body = LogMealPlanSlotRequest(
        date="2026-11-02",
        meal_type="dinner",
        expected_recipe_id="com-tam",
        portion_multiplier=1.5,
        rice_steps=2,
    )

    assert (body.portion_multiplier, body.rice_steps) == (1.5, 2)
    assert (
        LogMealPlanSlotRequest(
            date="2026-11-02", meal_type="dinner", expected_recipe_id="x"
        ).rice_steps
        == 0
    )


@pytest.mark.parametrize("steps", [-1, 5])
def test_the_log_request_refuses_impossible_rice(steps):
    from pydantic import ValidationError

    from src.api.schemas.request.weekly_meal_planner_requests import (
        LogMealPlanSlotRequest,
    )

    with pytest.raises(ValidationError):
        LogMealPlanSlotRequest(
            date="2026-11-02",
            meal_type="dinner",
            expected_recipe_id="x",
            rice_steps=steps,
        )


def test_rice_becomes_its_own_line_on_the_logged_meal():
    from src.app.services.recommended_meal_materialization_service import (
        RICE_STEP_GRAMS,
        _rice_food_item,
    )

    assert _rice_food_item(0) is None

    rice = _rice_food_item(3)
    assert rice.quantity == 3 * RICE_STEP_GRAMS == 165
    assert rice.macros.carbs == pytest.approx(60.0)
    assert rice.macros.protein == pytest.approx(4.5)
    # Calories come from the canonical formula, not a stored number
    # (the property rounds to one decimal).
    assert rice.macros.total_calories == pytest.approx(
        4.5 * 4 + 60 * 4 + 0.45 * 9, abs=0.1
    )
