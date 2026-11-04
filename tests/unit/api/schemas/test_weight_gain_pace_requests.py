"""Every request that takes a lose-weight pace takes a gain pace on the same grid."""

from datetime import UTC, datetime
from typing import Annotated

import pytest
from pydantic import TypeAdapter, ValidationError

from src.api.schemas.request.onboarding_requests import OnboardingCompleteRequest
from src.api.schemas.request.tdee_requests import TdeeCalculationRequest
from src.api.schemas.request.user_profile_update_requests import (
    UpdateMetricsRequest,
)
from src.api.schemas.response.user_responses import UserMetricsResponse


def _gain_field(model) -> TypeAdapter:
    field = model.model_fields["weight_gain_kg_per_week"]
    return TypeAdapter(Annotated[field.annotation, *field.metadata])


@pytest.mark.parametrize(
    "model", [OnboardingCompleteRequest, TdeeCalculationRequest, UpdateMetricsRequest]
)
def test_gain_pace_runs_from_one_slider_step_to_one_kg(model):
    pace = _gain_field(model)
    assert pace.validate_python(None) is None
    assert pace.validate_python(0.01) == 0.01
    assert pace.validate_python(1.0) == 1.0
    for value in (0, 0.004, 1.01):
        with pytest.raises(ValidationError):
            pace.validate_python(value)


def test_metrics_update_accepts_a_gain_pace_but_still_forbids_unknown_fields():
    request = UpdateMetricsRequest(weight_gain_kg_per_week=0.5)

    assert request.weight_gain_kg_per_week == 0.5
    with pytest.raises(ValidationError):
        UpdateMetricsRequest(weight_gain_kg_per_week_typo=0.5)


def test_metrics_response_always_carries_the_gain_keys():
    response = UserMetricsResponse(
        user_id="u1",
        age=30,
        gender="male",
        height_cm=175.0,
        weight_kg=72.0,
        job_type="desk",
        training_days_per_week=3,
        training_minutes_per_session=45,
        fitness_goal="bulk",
        updated_at=datetime(2026, 11, 1, tzinfo=UTC),
    ).model_dump(mode="json")

    assert response["weight_gain_kg_per_week"] is None
    assert response["weight_gain_lb_per_week"] is None
