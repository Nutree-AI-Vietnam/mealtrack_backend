import pytest
from pydantic import ValidationError

from src.app.events.hydration.hydration_created_event import HydrationCreatedEvent
from src.app.events.integration_event import IntegrationEvent

EVENT_ID = "00000000-0000-4000-8000-000000000001"


def _hydration_event() -> HydrationCreatedEvent:
    return HydrationCreatedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="hydr_" + "a" * 32,
    )


def test_hydration_event_serializes_to_id_only_wire_envelope() -> None:
    payload = _hydration_event().to_payload()

    assert payload["version"] == 1
    assert payload["event_type"] == "hydration.created.v1"
    assert payload["producer"] == "mealtrack_backend"
    assert payload["environment"] == "staging"
    assert payload["aggregate_type"] == "hydration"
    assert payload["aggregate_id"].startswith("hydr_")
    assert "data" not in payload


def test_hydration_event_rejects_wrong_event_type() -> None:
    with pytest.raises(ValidationError):
        HydrationCreatedEvent(
            event_id=EVENT_ID,
            event_type="other.event.v1",
            aggregate_id="hydr_" + "a" * 32,
        )


def test_generic_event_rejects_invalid_event_id() -> None:
    with pytest.raises(ValidationError, match="event_id must be a UUID"):
        HydrationCreatedEvent(
            event_id="not-a-uuid",
            aggregate_id="hydr_" + "a" * 32,
        )


def test_generic_event_rejects_oversized_payload() -> None:
    with pytest.raises(ValidationError, match="exceeds"):
        IntegrationEvent(
            event_type="test.event.v1",
            aggregate_type="test",
            aggregate_id="test-1",
            data={"value": "x" * 40_000},
        )
