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
