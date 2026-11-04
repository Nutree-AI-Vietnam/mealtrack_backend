"""Every request that takes a lose-weight pace accepts only the slider's grid."""

from typing import Annotated

import pytest
from pydantic import TypeAdapter, ValidationError

from src.api.schemas.request.onboarding_requests import OnboardingCompleteRequest
from src.api.schemas.request.tdee_requests import TdeeCalculationRequest
from src.api.schemas.request.user_profile_update_requests import (
    UpdateMetricsRequest,
)


def _pace_field(model) -> TypeAdapter:
    field = model.model_fields["weight_loss_kg_per_week"]
    return TypeAdapter(Annotated[field.annotation, *field.metadata])


@pytest.mark.parametrize(
    "model", [OnboardingCompleteRequest, TdeeCalculationRequest, UpdateMetricsRequest]
)
def test_pace_runs_from_one_slider_step_to_one_kg(model):
    pace = _pace_field(model)
    assert pace.validate_python(None) is None
    assert pace.validate_python(0.01) == 0.01
    assert pace.validate_python(1.0) == 1.0
    # Below a 0.01 kg step the stored pace would round to 0 and break the
    # database's "> 0" check.
    for value in (0, 0.004, 1.01):
        with pytest.raises(ValidationError):
            pace.validate_python(value)
