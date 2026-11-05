import asyncio
from unittest.mock import AsyncMock

import pytest

from src.infra.adapters.best_effort_integration_event_publisher import (
    BestEffortIntegrationEventPublisher,
)


@pytest.mark.asyncio
async def test_best_effort_publisher_forwards_successful_publish() -> None:
    inner = AsyncMock()
    publisher = BestEffortIntegrationEventPublisher(inner)

    await publisher.publish({"event_type": "meal.created.v1", "aggregate_id": "m1"})
    await publisher.drain()

    inner.publish.assert_awaited_once_with(
        {"event_type": "meal.created.v1", "aggregate_id": "m1"}
    )


@pytest.mark.asyncio
async def test_best_effort_publisher_swallows_queue_failures() -> None:
    inner = AsyncMock()
    inner.publish.side_effect = RuntimeError(
        "Cloudflare Queue account, ID, and token are required"
    )
    publisher = BestEffortIntegrationEventPublisher(inner)

    await publisher.publish({"event_type": "meal.created.v1", "aggregate_id": "m1"})
    await publisher.drain()


@pytest.mark.asyncio
async def test_best_effort_publisher_does_not_wait_for_inner() -> None:
    started = asyncio.Event()
    released = asyncio.Event()

    async def slow_publish(_payload):
        started.set()
        await released.wait()

    inner = AsyncMock()
    inner.publish.side_effect = slow_publish
    publisher = BestEffortIntegrationEventPublisher(inner)

    await publisher.publish({"event_type": "meal.created.v1", "aggregate_id": "m1"})
    await asyncio.sleep(0)
    assert started.is_set()
    assert inner.publish.await_count == 1

    released.set()
    await publisher.drain()
