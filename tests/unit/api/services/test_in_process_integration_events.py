"""Which follow-up work the API process does for each of its own events."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from src.api.services import in_process_integration_events as events
from src.api.services.meal_insight_event_handler import MealInsightEventHandler
from src.app.events.in_process_event_router import InProcessIntegrationEventRouter


@pytest.fixture
def wired(monkeypatch):
    """A router wired with stub purges and a stub Firebase deletion."""
    cache = object()
    purges = {
        "profile": AsyncMock(),
        "cheat_day": AsyncMock(),
        "saved_suggestion": AsyncMock(),
    }
    monkeypatch.setattr(events, "apply_profile_cache_invalidation", purges["profile"])
    monkeypatch.setattr(
        events, "apply_cheat_day_cache_invalidation", purges["cheat_day"]
    )
    monkeypatch.setattr(
        events,
        "apply_saved_suggestion_cache_invalidation",
        purges["saved_suggestion"],
    )
    delete_firebase_account = AsyncMock()
    router = InProcessIntegrationEventRouter()
    events.register_in_process_event_handlers(
        cache_getter=lambda: cache,
        ai_getter=lambda: object(),
        delete_firebase_account=delete_firebase_account,
        router=router,
    )
    return router, cache, purges, delete_firebase_account


def _event(event_type: str, data: dict, aggregate_id: str = "agg-1") -> dict:
    return {"event_type": event_type, "aggregate_id": aggregate_id, "data": data}


def test_meal_events_go_to_the_insight_handler(wired) -> None:
    router, *_ = wired

    for event_type in events.MEAL_EVENTS:
        handlers = router._handlers[event_type]
        assert [type(handler) for handler in handlers] == [MealInsightEventHandler]


@pytest.mark.parametrize(
    "event_type",
    [
        "hydration.created.v1",
        "hydration.deleted.v1",
        "movement.created.v1",
        "subscription.lifecycle.v1",
        "meal_recommendation.generate.v1",
    ],
)
def test_events_the_write_path_already_finished_have_no_handler(
    wired, event_type
) -> None:
    router, *_ = wired

    assert not router.handles(event_type)


@pytest.mark.asyncio
async def test_account_deletion_deletes_the_firebase_account(wired) -> None:
    router, _, _, delete_firebase_account = wired

    await router.publish(
        _event("user.deleted.v1", {"user_id": "u1", "firebase_uid": "fb-1"})
    )
    await router.publish(_event("user.account_cleanup.v1", {"user_id": "u1"}))

    delete_firebase_account.assert_awaited_once_with("fb-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", events.PROFILE_EVENTS)
async def test_profile_events_purge_the_users_target_caches(wired, event_type) -> None:
    router, cache, purges, _ = wired

    await router.publish(_event(event_type, {"user_id": "u1"}))

    purges["profile"].assert_awaited_once_with(cache, "u1")


@pytest.mark.asyncio
async def test_cheat_day_events_purge_that_weeks_budget(wired) -> None:
    router, cache, purges, _ = wired

    await router.publish(
        _event("cheat_day.marked.v1", {"user_id": "u1", "date": "2026-11-05"})
    )
    await router.publish(
        _event("cheat_day.unmarked.v1", {"user_id": "u1", "date": "not-a-date"})
    )

    purges["cheat_day"].assert_awaited_once_with(cache, "u1", date(2026, 11, 5))


@pytest.mark.asyncio
async def test_saved_suggestion_events_purge_the_saved_list(wired) -> None:
    router, cache, purges, _ = wired

    await router.publish(
        _event("saved_suggestion.deleted.v1", {"user_id": "u1"}, aggregate_id="s1")
    )

    purges["saved_suggestion"].assert_awaited_once_with(cache, "u1")


def test_registering_again_replaces_the_handlers(wired) -> None:
    router, cache, *_ = wired

    events.register_in_process_event_handlers(
        cache_getter=lambda: cache,
        ai_getter=lambda: object(),
        delete_firebase_account=AsyncMock(),
        router=router,
    )

    assert len(router._handlers["user.deleted.v1"]) == 1
