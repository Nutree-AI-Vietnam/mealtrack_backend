"""Contract tests for paid web-funnel onboarding and claim requests."""

from datetime import date

import pytest
from pydantic import ValidationError

from src.api.schemas.request.web_funnel_claim_requests import (
    WebFunnelLeadCreateRequest,
)


def _payload() -> dict:
    return {
        "email": "buyer@example.com",
        "birth_year": 1995,
        "birth_month": 4,
        "birth_day": 20,
        "gender": "female",
        "height": 168,
        "weight": 62,
        "job_type": "desk",
        "training_days_per_week": 3,
        "training_minutes_per_session": 45,
        "goal": "recomp",
    }


def test_lead_snapshot_uses_mobile_dob_wire_fields_without_age() -> None:
    payload = _payload()
    request = WebFunnelLeadCreateRequest.model_validate(
        {"email": payload.pop("email"), "payload": payload}
    )

    assert request.payload.birth_date == date(1995, 4, 20)
    assert "age" not in request.payload.model_dump()


def test_lead_snapshot_rejects_client_supplied_age() -> None:
    payload = _payload()
    payload["payload"] = {
        key: value for key, value in payload.items() if key != "email"
    }
    payload["payload"]["age"] = 18

    with pytest.raises(ValidationError):
        WebFunnelLeadCreateRequest.model_validate(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"birth_year": date.today().year + 1},
        {"birth_month": 2, "birth_day": 30},
    ],
)
def test_lead_snapshot_rejects_future_or_impossible_dob(
    changes: dict[str, int],
) -> None:
    payload = _payload()
    payload.update(changes)

    with pytest.raises(ValidationError):
        WebFunnelLeadCreateRequest.model_validate(
            {"email": payload.pop("email"), "payload": payload}
        )


def test_lead_display_hints_stay_outside_the_snapshot() -> None:
    payload = _payload()
    request = WebFunnelLeadCreateRequest.model_validate(
        {
            "email": payload.pop("email"),
            "payload": payload,
            "locale": "en",
            "units": "imperial",
            "off_plan_moment": "morning",
        }
    )
    assert (request.locale, request.units, request.off_plan_moment) == (
        "en",
        "imperial",
        "morning",
    )
    assert "off_plan_moment" not in request.payload.model_dump()
    with pytest.raises(ValidationError):
        WebFunnelLeadCreateRequest.model_validate(
            {"email": "a@b.co", "payload": payload, "off_plan_moment": "midnight"}
        )
