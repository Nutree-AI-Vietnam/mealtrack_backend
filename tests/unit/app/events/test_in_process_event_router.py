"""In-process routing of integration events that replaces the Queue worker."""

from unittest.mock import AsyncMock

import pytest

from src.app.events.in_process_event_router import InProcessIntegrationEventRouter


@pytest.mark.asyncio
async def test_router_runs_every_handler_registered_for_the_event_type() -> None:
    router = InProcessIntegrationEventRouter()
    first, second, other = AsyncMock(), AsyncMock(), AsyncMock()
    router.register(["meal.created.v1"], first)
    router.register(["meal.created.v1", "meal.updated.v1"], second)
    router.register(["user.deleted.v1"], other)
    payload = {"event_type": "meal.created.v1", "event_id": "e1"}

    await router.publish(payload)

    first.assert_awaited_once_with(payload)
    second.assert_awaited_once_with(payload)
    other.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_handler_does_not_skip_the_next_one(caplog) -> None:
    router = InProcessIntegrationEventRouter()
    failing = AsyncMock(side_effect=RuntimeError("redis down"))
    following = AsyncMock()
    router.register(["user.profile_updated.v1"], failing)
    router.register(["user.profile_updated.v1"], following)

    with caplog.at_level("WARNING", logger="src.app.events.in_process_event_router"):
        await router.publish({"event_type": "user.profile_updated.v1"})

    following.assert_awaited_once()
    assert "in-process integration event handler failed" in caplog.text


@pytest.mark.asyncio
async def test_event_types_without_handlers_need_no_work() -> None:
    router = InProcessIntegrationEventRouter()
    handler = AsyncMock()
    router.register(["meal.created.v1"], handler)

    await router.publish({"event_type": "hydration.created.v1"})
    await router.publish({"no": "event_type"})

    handler.assert_not_awaited()
    assert not router.handles("hydration.created.v1")


def test_clear_removes_every_handler() -> None:
    router = InProcessIntegrationEventRouter()
    router.register(["meal.created.v1"], AsyncMock())

    router.clear()

    assert not router.handles("meal.created.v1")
