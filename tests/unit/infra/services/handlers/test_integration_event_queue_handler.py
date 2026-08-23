from unittest.mock import AsyncMock

import pytest

from src.app.events.integration_event import HydrationCreatedEvent
from src.domain.ports.outbox_handler_port import OutboxEventContext
from src.infra.adapters.cloudflare_queue_publisher import CloudflareQueueDisabledError
from src.infra.services.handlers.integration_event_queue_handler import (
    IntegrationEventQueueHandler,
)


def _event() -> HydrationCreatedEvent:
    hydration_id = "hydr_" + "a" * 32
    return HydrationCreatedEvent(
        event_id="00000000-0000-4000-8000-000000000001",
        aggregate_id=hydration_id,
        data={
            "user_id": "00000000-0000-4000-8000-000000000002",
            "hydration_id": hydration_id,
            "meal_id": "meal-1",
            "drink_id": "drink-1",
            "drink_name": "Water",
            "volume_ml": 500,
            "credited_ml": 500,
            "logged_at": "2026-08-23T00:00:00Z",
            "log_date": "2026-08-23",
        },
    )


def _context(event: HydrationCreatedEvent) -> OutboxEventContext:
    return OutboxEventContext(
        outbox_id="outbox-1",
        event_id=event.event_id,
        event_type=event.event_type,
        retry_count=0,
        created_at_iso=event.occurred_at.isoformat(),
    )


@pytest.mark.asyncio
async def test_valid_event_is_published_to_ingress_queue() -> None:
    publisher = AsyncMock()
    publisher.queue_name = "mealtrack-events-staging"
    event = _event()

    result = await IntegrationEventQueueHandler(
        publisher, HydrationCreatedEvent
    ).handle(event.to_payload(), _context(event))

    assert result.success is True
    publisher.publish.assert_awaited_once_with(event.to_payload())
    assert result.metadata["queue"] == "mealtrack-events-staging"


@pytest.mark.asyncio
async def test_invalid_event_is_permanent_failure_without_publish() -> None:
    publisher = AsyncMock()
    result = await IntegrationEventQueueHandler(
        publisher, HydrationCreatedEvent
    ).handle({"event_type": "hydration.created.v1"}, _context(_event()))

    assert result.success is False
    assert result.is_transient is False
    assert result.error_type == "InvalidIntegrationEvent"
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_context_mismatch_is_permanent_failure() -> None:
    publisher = AsyncMock()
    event = _event()
    context = _context(event)
    mismatched = OutboxEventContext(
        outbox_id=context.outbox_id,
        event_id="different-event",
        event_type=context.event_type,
        retry_count=context.retry_count,
        created_at_iso=context.created_at_iso,
    )

    result = await IntegrationEventQueueHandler(
        publisher, HydrationCreatedEvent
    ).handle(event.to_payload(), mismatched)

    assert result.success is False
    assert result.error_type == "IntegrationEventContextMismatch"
    publisher.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_ingress_publication_pauses_without_consuming_retry_budget() -> (
    None
):
    publisher = AsyncMock()
    publisher.publish.side_effect = CloudflareQueueDisabledError("disabled")
    event = _event()

    result = await IntegrationEventQueueHandler(
        publisher, HydrationCreatedEvent
    ).handle(event.to_payload(), _context(event))

    assert result.success is False
    assert result.is_paused is True
    assert result.is_transient is True
