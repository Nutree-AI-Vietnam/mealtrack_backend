import pytest
from pydantic import ValidationError

from src.app.events.hydration import (
    HydrateDeletedEvent,
    HydrationCaloricCreatedEvent,
    HydrationCaloricDeletedEvent,
    HydrationCreatedEvent,
    HydrationDeletedEvent,
)
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


def test_hydration_caloric_created_event_serialization() -> None:
    event = HydrationCaloricCreatedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="hydr_" + "b" * 32,
    )
    payload = event.to_payload()
    assert payload["event_type"] == "hydration.caloric_created.v1"
    assert payload["aggregate_type"] == "hydration"
    assert payload["aggregate_id"] == "hydr_" + "b" * 32


def test_hydration_deleted_event_serialization() -> None:
    event = HydrationDeletedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="hydr_" + "c" * 32,
        data={"user_id": "user-1", "log_date": "2026-08-23"},
    )
    payload = event.to_payload()
    assert payload["event_type"] == "hydration.deleted.v1"
    assert payload["aggregate_type"] == "hydration"
    assert payload["data"] == {"user_id": "user-1", "log_date": "2026-08-23"}

    # Alias check
    assert HydrateDeletedEvent is HydrationDeletedEvent


def test_hydration_caloric_deleted_event_serialization() -> None:
    event = HydrationCaloricDeletedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="hydr_" + "d" * 32,
        data={"user_id": "user-1", "log_date": "2026-08-23"},
    )
    payload = event.to_payload()
    assert payload["event_type"] == "hydration.caloric_deleted.v1"
    assert payload["aggregate_type"] == "hydration"
    assert payload["data"] == {"user_id": "user-1", "log_date": "2026-08-23"}


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


from src.app.events.movement import (
    MovementCreatedEvent,
    MovementDeletedEvent,
    MovementUpdatedEvent,
)
from src.app.events.user import (
    UserCustomMacrosUpdatedEvent,
    UserOnboardingCompletedEvent,
    UserProfileUpdatedIntegrationEvent,
)


def test_movement_events_serialization() -> None:
    create_evt = MovementCreatedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="mov-1",
        data={"user_id": "user-1", "log_date": "2026-08-23"},
    )
    p1 = create_evt.to_payload()
    assert p1["event_type"] == "movement.created.v1"
    assert p1["aggregate_type"] == "movement"
    assert p1["aggregate_id"] == "mov-1"
    assert p1["data"] == {"user_id": "user-1", "log_date": "2026-08-23"}

    upd_evt = MovementUpdatedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="mov-1",
        data={"user_id": "user-1", "log_date": "2026-08-23"},
    )
    p2 = upd_evt.to_payload()
    assert p2["event_type"] == "movement.updated.v1"
    assert p2["aggregate_type"] == "movement"

    del_evt = MovementDeletedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="mov-1",
        data={"user_id": "user-1", "log_date": "2026-08-23"},
    )
    p3 = del_evt.to_payload()
    assert p3["event_type"] == "movement.deleted.v1"
    assert p3["aggregate_type"] == "movement"


def test_user_events_serialization() -> None:
    prof_evt = UserProfileUpdatedIntegrationEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="user-1",
        data={"user_id": "user-1"},
    )
    p1 = prof_evt.to_payload()
    assert p1["event_type"] == "user.profile_updated.v1"
    assert p1["aggregate_type"] == "user"
    assert p1["aggregate_id"] == "user-1"

    onb_evt = UserOnboardingCompletedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="user-1",
        data={"user_id": "user-1"},
    )
    p2 = onb_evt.to_payload()
    assert p2["event_type"] == "user.onboarding_completed.v1"
    assert p2["aggregate_type"] == "user"

    macro_evt = UserCustomMacrosUpdatedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="user-1",
        data={"user_id": "user-1"},
    )
    p3 = macro_evt.to_payload()
    assert p3["event_type"] == "user.custom_macros_updated.v1"
    assert p3["aggregate_type"] == "user"


from src.app.events.cheat_day import (
    CheatDayMarkedEvent,
    CheatDayUnmarkedEvent,
)
from src.app.events.saved_suggestion import (
    SavedSuggestionCreatedEvent,
    SavedSuggestionDeletedEvent,
)


def test_cheat_day_events_serialization() -> None:
    marked_evt = CheatDayMarkedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="cheat-1",
        data={"user_id": "user-1", "date": "2026-08-23"},
    )
    p1 = marked_evt.to_payload()
    assert p1["event_type"] == "cheat_day.marked.v1"
    assert p1["aggregate_type"] == "cheat_day"
    assert p1["aggregate_id"] == "cheat-1"
    assert p1["data"] == {"user_id": "user-1", "date": "2026-08-23"}

    unmarked_evt = CheatDayUnmarkedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="cheat-1",
        data={"user_id": "user-1", "date": "2026-08-23"},
    )
    p2 = unmarked_evt.to_payload()
    assert p2["event_type"] == "cheat_day.unmarked.v1"
    assert p2["aggregate_type"] == "cheat_day"
    assert p2["aggregate_id"] == "cheat-1"


def test_saved_suggestion_events_serialization() -> None:
    created_evt = SavedSuggestionCreatedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="sug-1",
        data={"user_id": "user-1"},
    )
    p1 = created_evt.to_payload()
    assert p1["event_type"] == "saved_suggestion.created.v1"
    assert p1["aggregate_type"] == "saved_suggestion"
    assert p1["aggregate_id"] == "sug-1"
    assert p1["data"] == {"user_id": "user-1"}

    del_evt = SavedSuggestionDeletedEvent(
        event_id=EVENT_ID,
        environment="staging",
        aggregate_id="sug-1",
        data={"user_id": "user-1"},
    )
    p2 = del_evt.to_payload()
    assert p2["event_type"] == "saved_suggestion.deleted.v1"
    assert p2["aggregate_type"] == "saved_suggestion"


def test_generic_event_rejects_oversized_payload() -> None:
    with pytest.raises(ValidationError, match="exceeds"):
        IntegrationEvent(
            event_type="test.event.v1",
            aggregate_type="test",
            aggregate_id="test-1",
            data={"value": "x" * 40_000},
        )


