import json
from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.domain.model.meal_recommendation import CatalogMeal
from src.domain.model.weekly_meal_planner import (
    WeeklyMealPlan,
    WeeklyMealPlanPreferences,
    WeeklyMealPlanSlot,
    WeeklyMealPlanStatus,
)
from src.infra.adapters.weekly_meal_plan_adjustment_provider import (
    StructuredWeeklyMealPlanAdjustmentProvider,
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


def _plan():
    return WeeklyMealPlan(
        id="plan",
        user_id="user",
        week_start_date=date(2026, 9, 28),
        status=WeeklyMealPlanStatus.DRAFT,
        people=1,
        preferences=WeeklyMealPlanPreferences(people=1),
        timezone="UTC",
        slots=tuple(
            WeeklyMealPlanSlot(
                id=f"{day}-{slot}", day_index=day, slot_index=slot, recipe_id=None
            )
            for day in range(7)
            for slot in range(2)
        ),
        created_at=datetime(2026, 9, 28),
        updated_at=datetime(2026, 9, 28),
    )


def _meal():
    return CatalogMeal(
        id="recipe",
        catalog_key="recipe",
        content_hash="a" * 64,
        name="Recipe",
        cuisine="vietnamese",
        description=None,
        image_url=None,
        protein_g=30,
        carbs_g=40,
        fat_g=10,
        fiber_g=4,
    )


@pytest.mark.asyncio
async def test_planner_adapter_localizes_explanation_and_diff_in_one_structured_response():
    service = AsyncMock()
    service.generate_meal_plan_async.return_value = {
        "explanation": "Đổi một bữa tối.",
        "diff_summary": "1 bữa được đổi",
        "slot_changes": [
            {
                "day_index": 0,
                "slot_index": 1,
                "action": "replace",
                "new_recipe_id": "recipe",
            }
        ],
    }
    proposal = await StructuredWeeklyMealPlanAdjustmentProvider(service).propose(
        prompt="đổi món", plan=_plan(), meals=(_meal(),), language="vi"
    )
    assert proposal.diff_summary == "1 bữa được đổi"
    assert proposal.explanation == "Đổi một bữa tối."
    assert service.generate_meal_plan_async.await_count == 1
    kwargs = service.generate_meal_plan_async.await_args.kwargs
    assert kwargs["model_purpose"] == "meal_plan_adjustment"
    assert kwargs["max_tokens"] == 1800
    context = json.loads(kwargs["prompt"].split("Plan context JSON: ", 1)[1])
    assert context["response_language"] == "vi"
    assert context["available_recipes"][0]["id"] == "recipe"


@pytest.mark.asyncio
async def test_planner_adapter_rejects_oversize_shortlist_without_silent_truncation():
    service = AsyncMock()
    with pytest.raises(ValueError, match="40"):
        await StructuredWeeklyMealPlanAdjustmentProvider(service).propose(
            prompt="request", plan=_plan(), meals=(_meal(),) * 41
        )
    service.generate_meal_plan_async.assert_not_called()


@pytest.mark.asyncio
async def test_planner_adapter_expired_explicit_deadline_never_calls_provider():
    service = AsyncMock()
    with pytest.raises(TimeoutError):
        await StructuredWeeklyMealPlanAdjustmentProvider(service).propose(
            prompt="request", plan=_plan(), meals=(), deadline=0
        )
    service.generate_meal_plan_async.assert_not_called()
