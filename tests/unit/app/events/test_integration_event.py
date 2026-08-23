from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from src.app.events.integration_event import (
    HydrationCreatedData,
    HydrationCreatedEvent,
    IntegrationEvent,
)

EVENT_ID = "00000000-0000-4000-8000-000000000001"
USER_ID = "00000000-0000-4000-8000-000000000002"


def _hydration_event() -> HydrationCreatedEvent:
    hydration_id = "hydr_" + "a" * 32
    return HydrationCreatedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id=hydration_id,
        data=HydrationCreatedData(
            user_id=USER_ID,
            hydration_id=hydration_id,
            meal_id="meal-1",
            drink_id="drink-1",
            drink_name="Water",
            emoji="💧",
            volume_ml=500,
            credited_ml=500,
            logged_at=datetime(2026, 8, 23, tzinfo=UTC),
            log_date=date(2026, 8, 23),
        ),
    )


def test_hydration_event_serializes_to_stable_wire_envelope() -> None:
    payload = _hydration_event().to_payload()

    assert payload["version"] == 1
    assert payload["event_type"] == "hydration.created.v1"
    assert payload["producer"] == "mealtrack_backend"
    assert payload["environment"] == "staging"
    assert payload["aggregate_type"] == "hydration"
    assert payload["data"]["hydration_id"].startswith("hydr_")
    assert "tokens" not in payload["data"]


def test_hydration_event_requires_matching_aggregate_identity() -> None:
    with pytest.raises(ValidationError, match="aggregate_id must match"):
        HydrationCreatedEvent(
            event_id=EVENT_ID,
            aggregate_id="hydr_" + "b" * 32,
            data={
                "user_id": USER_ID,
                "hydration_id": "hydr_" + "a" * 32,
                "meal_id": "meal-1",
                "drink_id": "drink-1",
                "drink_name": "Water",
                "volume_ml": 500,
                "credited_ml": 500,
                "logged_at": "2026-08-23T00:00:00Z",
                "log_date": "2026-08-23",
            },
        )


def test_hydration_event_rejects_unknown_fields_and_invalid_identity() -> None:
    with pytest.raises(ValidationError):
        HydrationCreatedEvent(
            event_id=EVENT_ID,
            aggregate_id="hydr_not-a-valid-id",
            data={
                "user_id": USER_ID,
                "hydration_id": "hydr_not-a-valid-id",
                "meal_id": "meal-1",
                "drink_id": "drink-1",
                "drink_name": "Water",
                "volume_ml": 500,
                "credited_ml": 500,
                "logged_at": "2026-08-23T00:00:00Z",
                "log_date": "2026-08-23",
                "access_token": "must-not-cross-boundary",
            },
        )


def test_generic_event_rejects_oversized_payload() -> None:
    with pytest.raises(ValidationError, match="exceeds"):
        IntegrationEvent(
            event_type="test.event.v1",
            aggregate_type="test",
            aggregate_id="test-1",
            data={"value": "x" * 40_000},
        )
