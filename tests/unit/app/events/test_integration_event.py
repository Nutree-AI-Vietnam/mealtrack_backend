from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
from src.app.events.meal.meal_events import (
    MealCreatedEvent,
    MealInsightSnapshot,
    publish_meal_event,
)
from src.domain.model.nutrition import FoodItem, Macros, Nutrition

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
    UserProfileUpdatedEvent,
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
    prof_evt = UserProfileUpdatedEvent(
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


def test_meal_created_event_embeds_bounded_insight_snapshot() -> None:
    meal = SimpleNamespace(
        meal_id="00000000-0000-4000-8000-000000000002",
        user_id="00000000-0000-4000-8000-000000000003",
        dish_name="Chicken rice",
        created_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        ready_at=None,
        updated_at=datetime(2026, 8, 24, 11, 0, tzinfo=UTC),
        nutrition=Nutrition(
            macros=Macros(protein=28, carbs=52, fat=8, fiber=2, sugar=1),
            food_items=[
                FoodItem(
                    id="food-1",
                    name="Chicken",
                    quantity=150,
                    unit="g",
                    macros=Macros(protein=24, carbs=0, fat=5, fiber=0, sugar=0),
                    confidence=0.9,
                )
            ],
        ),
    )

    snapshot = MealInsightSnapshot.from_meal(
        meal,
        language="vi-VN",
    ).model_dump(mode="json", exclude_none=True)
    payload = MealCreatedEvent(
        environment="staging",
        aggregate_id=meal.meal_id,
        occurred_at=meal.updated_at,
        data={
            "user_id": meal.user_id,
            "meal_id": meal.meal_id,
            "meal_date": "2026-08-24",
            "insight": snapshot,
        },
    ).to_payload()

    assert payload["version"] == 1
    assert payload["event_type"] == "meal.created.v1"
    assert payload["data"]["user_id"] == meal.user_id
    assert payload["data"]["meal_id"] == meal.meal_id
    assert payload["data"]["insight"]["language"] == "vi"
    assert payload["occurred_at"] == "2026-08-24T11:00:00Z"
    assert payload["data"]["insight"]["nutrition"]["protein_g"] == 28
    assert payload["data"]["insight"]["ingredients"][0]["name"] == "Chicken"


@pytest.mark.asyncio
async def test_meal_event_publish_includes_compact_profile_context() -> None:
    meal = SimpleNamespace(
        user_id="00000000-0000-4000-8000-000000000003",
        meal_id="00000000-0000-4000-8000-000000000004",
        dish_name="Chicken rice",
        created_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
        ready_at=None,
        updated_at=None,
        nutrition=Nutrition(
            macros=Macros(protein=28, carbs=52, fat=8, fiber=2, sugar=1),
            food_items=[],
        ),
    )
    publisher = SimpleNamespace(publish=AsyncMock())
    event_bus = SimpleNamespace(
        send=AsyncMock(
            return_value={
                "profile": {
                    "fitness_goal": "weight_loss",
                    "allergies": ["peanuts"],
                    "unused_field": "not forwarded",
                },
                "tdee": {"target_calories": 2000},
            }
        )
    )

    assert await publish_meal_event(
        publisher,
        meal,
        event_type="created",
        environment="staging",
        meal_date=datetime(2026, 8, 24, tzinfo=UTC).date(),
        event_bus=event_bus,
    )

    payload = publisher.publish.await_args.args[0]
    assert payload["event_type"] == "meal.created.v1"
    assert payload["aggregate_id"] == meal.meal_id
    assert payload["data"]["user_id"] == meal.user_id
    assert payload["data"]["meal_id"] == meal.meal_id
    assert payload["data"]["meal_date"] == "2026-08-24"
    insight = payload["data"]["insight"]
    assert insight["language"] == "en"
    assert insight["user_context"]["fitness_goal"] == "weight_loss"
    assert insight["user_context"]["allergies"] == ["peanuts"]
    assert insight["user_context"]["target_calories"] == 2000
    assert "unused_field" not in insight["user_context"]
