import pytest
from pydantic import ValidationError

from src.infra.adapters.weekly_meal_plan_adjustment_provider import (
    WeeklyMealPlanAdjustmentResponse,
)


def test_weekly_adjustment_schema_exposes_supported_actions_as_enum():
    slot_schema = WeeklyMealPlanAdjustmentResponse.model_json_schema()["$defs"][
        "WeeklyMealPlanSlotAdjustmentResponse"
    ]

    assert slot_schema["properties"]["action"]["enum"] == ["replace", "clear"]


def test_weekly_adjustment_rejects_noncanonical_action():
    with pytest.raises(ValidationError):
        WeeklyMealPlanAdjustmentResponse(
            explanation="Replace a meal",
            slot_changes=[
                {
                    "day_index": 2,
                    "slot_index": 1,
                    "action": "replace_meal",
                    "new_recipe_id": "tofu",
                }
            ],
        )


def test_weekly_adjustment_accepts_week_sized_explanation_payloads():
    response = WeeklyMealPlanAdjustmentResponse(
        explanation="x" * 800,
        slot_changes=[],
    )

    assert len(response.explanation) == 800
