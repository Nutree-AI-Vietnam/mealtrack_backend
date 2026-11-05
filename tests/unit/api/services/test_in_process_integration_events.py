"""Which follow-up work the API process does for each of its own events."""

import asyncio
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
        assert [type(handler.handler) for handler in handlers] == [
            MealInsightEventHandler
        ]


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


class _Held:
    """A handler stand-in that records how many calls run at once."""

    def __init__(self) -> None:
        self.calls = 0
        self.running = 0
        self.peak = 0
        self.release = asyncio.Event()

    async def __call__(self, *_: object) -> None:
        self.calls += 1
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await self.release.wait()
        finally:
            self.running -= 1


async def _peak_while_held(router, payloads: list[dict], held: _Held) -> int:
    """Publish every payload at once and return how many ran before release."""
    tasks = [asyncio.create_task(router.publish(payload)) for payload in payloads]
    for _ in range(10):
        await asyncio.sleep(0)
    peak = held.peak
    held.release.set()
    await asyncio.gather(*tasks)
    return peak


def _register(router, *, delete_firebase_account=None) -> None:
    events.register_in_process_event_handlers(
        cache_getter=lambda: object(),
        ai_getter=lambda: object(),
        delete_firebase_account=delete_firebase_account or AsyncMock(),
        router=router,
    )


@pytest.mark.asyncio
async def test_meal_insights_run_at_most_32_at_once_per_worker(monkeypatch) -> None:
    held = _Held()
    monkeypatch.setattr(events, "MealInsightEventHandler", lambda *_: held)
    router = InProcessIntegrationEventRouter()
    _register(router)

    burst = [_event("meal.created.v1", {"meal_id": f"m{i}"}) for i in range(40)]

    assert await _peak_while_held(router, burst, held) == 32
    assert events.MEAL_INSIGHT_CONCURRENCY == 32
    assert held.calls == 40


@pytest.mark.asyncio
async def test_cache_purges_share_two_slots_per_worker(monkeypatch) -> None:
    held = _Held()
    for purge in (
        "apply_profile_cache_invalidation",
        "apply_cheat_day_cache_invalidation",
        "apply_saved_suggestion_cache_invalidation",
    ):
        monkeypatch.setattr(events, purge, held)
    router = InProcessIntegrationEventRouter()
    _register(router)

    burst = [_event("user.profile_updated.v1", {"user_id": f"u{i}"}) for i in range(4)]
    burst += [
        _event("cheat_day.marked.v1", {"user_id": "u9", "date": "2026-11-05"}),
        _event("saved_suggestion.created.v1", {"user_id": "u9"}),
    ]

    assert await _peak_while_held(router, burst, held) == 2
    assert held.calls == 6


@pytest.mark.asyncio
async def test_firebase_deletions_use_at_most_two_firebase_threads() -> None:
    held = _Held()
    router = InProcessIntegrationEventRouter()
    _register(router, delete_firebase_account=held)

    burst = [_event("user.deleted.v1", {"firebase_uid": f"fb-{i}"}) for i in range(5)]

    assert await _peak_while_held(router, burst, held) == 2
    assert held.calls == 5


@pytest.mark.asyncio
async def test_a_failing_limited_handler_frees_its_slot_and_keeps_its_name(
    monkeypatch, caplog
) -> None:
    async def apply_profile_cache_invalidation(*_: object) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        events, "apply_profile_cache_invalidation", apply_profile_cache_invalidation
    )
    router = InProcessIntegrationEventRouter()
    _register(router)

    for i in range(3):
        await router.publish(_event("user.profile_updated.v1", {"user_id": f"u{i}"}))

    failures = [
        record
        for record in caplog.records
        if record.getMessage() == "in-process integration event handler failed"
    ]
    assert len(failures) == 3
    assert {record.handler for record in failures} == {"purge_profile"}


def test_registering_again_replaces_the_handlers(wired) -> None:
    router, cache, *_ = wired

    events.register_in_process_event_handlers(
        cache_getter=lambda: cache,
        ai_getter=lambda: object(),
        delete_firebase_account=AsyncMock(),
        router=router,
    )

    assert len(router._handlers["user.deleted.v1"]) == 1
