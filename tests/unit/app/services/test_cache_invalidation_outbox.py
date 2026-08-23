from datetime import date

import pytest
from tests.fixtures.fakes.fake_outbox_repository import FakeOutboxRepository

from src.app.services.cache_invalidation_service import (
    CACHE_INVALIDATION_EVENT_TYPE,
    CacheInvalidationService,
)


@pytest.mark.asyncio
async def test_enqueue_meal_invalidation_persists_minimal_event() -> None:
    service = CacheInvalidationService(object())
    outbox = FakeOutboxRepository()
    user_id = "00000000-0000-4000-8000-000000000002"

    event_id = await service.enqueue_meal_invalidation(
        outbox,
        user_id,
        date(2026, 6, 2),
        event_id="00000000-0000-4000-8000-000000000001",
        current_date=date(2026, 6, 3),
    )

    assert event_id == "00000000-0000-4000-8000-000000000001"
    assert len(outbox.enqueue_calls) == 1
    call = outbox.enqueue_calls[0]
    assert call["event_type"] == CACHE_INVALIDATION_EVENT_TYPE
    assert call["event_id"] == "00000000-0000-4000-8000-000000000001"
    assert call["aggregate_type"] == "user"
    assert call["aggregate_id"] == user_id
    assert call["payload"]["version"] == 1
    assert call["payload"]["event_type"] == CACHE_INVALIDATION_EVENT_TYPE
    assert call["payload"]["event_id"] == "00000000-0000-4000-8000-000000000001"
    assert call["payload"]["operations"]


@pytest.mark.asyncio
async def test_enqueue_hydration_invalidation_persists_event() -> None:
    service = CacheInvalidationService(object())
    outbox = FakeOutboxRepository()
    user_id = "00000000-0000-4000-8000-000000000002"

    event_id = await service.enqueue_hydration_invalidation(
        outbox,
        user_id,
        date(2026, 6, 2),
        event_id="00000000-0000-4000-8000-000000000003",
    )

    assert event_id == "00000000-0000-4000-8000-000000000003"
    assert len(outbox.enqueue_calls) == 1
    call = outbox.enqueue_calls[0]
    assert call["event_type"] == CACHE_INVALIDATION_EVENT_TYPE
    assert call["aggregate_id"] == user_id
    ops = call["payload"]["operations"]
    assert any(
        op.get("pattern", "").startswith(f"user:{user_id}:hydration:") for op in ops
    )


@pytest.mark.asyncio
async def test_enqueue_movement_invalidation_persists_event() -> None:
    service = CacheInvalidationService(object())
    outbox = FakeOutboxRepository()
    user_id = "00000000-0000-4000-8000-000000000002"

    event_id = await service.enqueue_movement_invalidation(
        outbox,
        user_id,
        date(2026, 6, 2),
        event_id="00000000-0000-4000-8000-000000000004",
    )

    assert event_id == "00000000-0000-4000-8000-000000000004"
    assert len(outbox.enqueue_calls) == 1
    call = outbox.enqueue_calls[0]
    assert call["event_type"] == CACHE_INVALIDATION_EVENT_TYPE
    assert call["aggregate_id"] == user_id


@pytest.mark.asyncio
async def test_enqueue_profile_invalidation_persists_event() -> None:
    service = CacheInvalidationService(object())
    outbox = FakeOutboxRepository()
    user_id = "00000000-0000-4000-8000-000000000002"

    event_id = await service.enqueue_profile_invalidation(
        outbox,
        user_id,
        event_id="00000000-0000-4000-8000-000000000005",
    )

    assert event_id == "00000000-0000-4000-8000-000000000005"
    assert len(outbox.enqueue_calls) == 1
    call = outbox.enqueue_calls[0]
    assert call["event_type"] == CACHE_INVALIDATION_EVENT_TYPE
    assert call["aggregate_id"] == user_id


@pytest.mark.asyncio
async def test_enqueue_cheat_day_invalidation_persists_event() -> None:
    service = CacheInvalidationService(object())
    outbox = FakeOutboxRepository()
    user_id = "00000000-0000-4000-8000-000000000002"

    event_id = await service.enqueue_cheat_day_invalidation(
        outbox,
        user_id,
        date(2026, 6, 2),
        event_id="00000000-0000-4000-8000-000000000006",
    )

    assert event_id == "00000000-0000-4000-8000-000000000006"
    assert len(outbox.enqueue_calls) == 1


@pytest.mark.asyncio
async def test_enqueue_saved_suggestion_invalidation_persists_event() -> None:
    service = CacheInvalidationService(object())
    outbox = FakeOutboxRepository()
    user_id = "00000000-0000-4000-8000-000000000002"

    event_id = await service.enqueue_saved_suggestion_invalidation(
        outbox,
        user_id,
        event_id="00000000-0000-4000-8000-000000000007",
    )

    assert event_id == "00000000-0000-4000-8000-000000000007"
    assert len(outbox.enqueue_calls) == 1


@pytest.mark.asyncio
async def test_enqueue_meal_invalidation_skips_when_cache_is_disabled() -> None:
    service = CacheInvalidationService(None)
    outbox = FakeOutboxRepository()

    result = await service.enqueue_meal_invalidation(
        outbox,
        "user1",
        date(2026, 6, 2),
    )

    assert result is None
    assert outbox.enqueue_calls == []


@pytest.mark.asyncio
async def test_enqueue_meal_invalidation_skips_when_queue_is_disabled() -> None:
    service = CacheInvalidationService(object(), queue_enabled=False)
    outbox = FakeOutboxRepository()

    result = await service.enqueue_meal_invalidation(
        outbox,
        "user1",
        date(2026, 6, 2),
    )

    assert result is None
    assert outbox.enqueue_calls == []


@pytest.mark.asyncio
async def test_enqueue_meal_invalidation_rejects_non_uuid_user_id() -> None:
    service = CacheInvalidationService(object())
    outbox = FakeOutboxRepository()

    with pytest.raises(ValueError, match="user_id must be UUID"):
        await service.enqueue_meal_invalidation(
            outbox,
            "user1",
            date(2026, 6, 2),
        )
