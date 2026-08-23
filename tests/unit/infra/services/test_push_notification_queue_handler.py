"""Unit tests for PushNotificationQueueHandler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.ports.outbox_handler_port import OutboxEventContext
from src.infra.adapters.cloudflare_queue_publisher import (
    CloudflareQueuePermanentError,
    CloudflareQueuePublisher,
    CloudflareQueueTransientError,
)
from src.infra.services.handlers.push_notification_queue_handler import (
    PushNotificationQueueHandler,
)


@pytest.mark.asyncio
async def test_push_queue_handler_publishes_event_successfully() -> None:
    publisher = MagicMock(spec=CloudflareQueuePublisher)
    publisher.publish = AsyncMock()
    publisher._queue_name = "mealtrack-events-staging"

    handler = PushNotificationQueueHandler(publisher)
    context = OutboxEventContext(
        outbox_id="outbox_123",
        event_id="e8e03e42-70b9-4fcf-b673-982ee33190df",
        event_type="push_notification",
        retry_count=0,
        created_at_iso="2026-08-22T10:00:00Z",
    )

    payload = {
        "title": "Lunch Reminder",
        "body": "Log your lunch",
        "tokens": ["fcm_token_1"],
    }

    result = await handler.handle(payload, context)

    assert result.success is True
    assert result.metadata["destination"] == "cloudflare_queue"
    publisher.publish.assert_awaited_once()

    published_payload = publisher.publish.call_args[0][0]
    assert published_payload["event_type"] == "push_notification.v1"
    assert published_payload["version"] == 1
    assert published_payload["event_id"] == "e8e03e42-70b9-4fcf-b673-982ee33190df"
    assert published_payload["tokens"] == ["fcm_token_1"]


@pytest.mark.asyncio
async def test_push_queue_handler_handles_transient_queue_failure() -> None:
    publisher = MagicMock(spec=CloudflareQueuePublisher)
    publisher.publish = AsyncMock(
        side_effect=CloudflareQueueTransientError("Queue timed out")
    )
    publisher._queue_name = "mealtrack-events"

    handler = PushNotificationQueueHandler(publisher)
    context = OutboxEventContext(
        outbox_id="outbox_123",
        event_id="e8e03e42-70b9-4fcf-b673-982ee33190df",
        event_type="push_notification",
        retry_count=0,
        created_at_iso="2026-08-22T10:00:00Z",
    )

    result = await handler.handle({"title": "A", "body": "B"}, context)

    assert result.success is False
    assert result.is_transient is True
    assert "Queue timed out" in (result.error_message or "")


@pytest.mark.asyncio
async def test_push_queue_handler_handles_permanent_queue_failure() -> None:
    publisher = MagicMock(spec=CloudflareQueuePublisher)
    publisher.publish = AsyncMock(
        side_effect=CloudflareQueuePermanentError("Queue rejected request")
    )
    publisher._queue_name = "mealtrack-events"

    handler = PushNotificationQueueHandler(publisher)
    context = OutboxEventContext(
        outbox_id="outbox_123",
        event_id="e8e03e42-70b9-4fcf-b673-982ee33190df",
        event_type="push_notification",
        retry_count=0,
        created_at_iso="2026-08-22T10:00:00Z",
    )

    result = await handler.handle({"title": "A", "body": "B"}, context)

    assert result.success is False
    assert result.is_transient is False
    assert "Queue rejected request" in (result.error_message or "")
