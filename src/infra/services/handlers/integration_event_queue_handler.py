"""Outbox handler for validated cross-repository integration events."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from src.app.events.integration_event import IntegrationEvent
from src.domain.ports.outbox_handler_port import (
    OutboxEventContext,
    OutboxEventHandler,
    OutboxHandlerResult,
)
from src.infra.adapters.cloudflare_queue_publisher import (
    CloudflareQueueConfigurationError,
    CloudflareQueueDisabledError,
    CloudflareQueuePermanentError,
    CloudflareQueuePublisher,
    CloudflareQueueTransientError,
)


class IntegrationEventQueueHandler(OutboxEventHandler):
    """Validate and publish one integration event to the ingress Queue."""

    def __init__(
        self,
        publisher: CloudflareQueuePublisher,
        event_model: type[IntegrationEvent] = IntegrationEvent,
    ) -> None:
        self._publisher = publisher
        self._event_model = event_model

    async def handle(
        self,
        payload: dict[str, Any],
        context: OutboxEventContext,
    ) -> OutboxHandlerResult:
        try:
            event = self._event_model.model_validate(payload)
        except ValidationError as exc:
            return OutboxHandlerResult.permanent_failure(
                "Integration event failed schema validation",
                error_type="InvalidIntegrationEvent",
                metadata={"validation_errors": exc.error_count()},
            )

        if event.event_id != context.event_id or event.event_type != context.event_type:
            return OutboxHandlerResult.permanent_failure(
                "Integration event does not match its outbox context",
                error_type="IntegrationEventContextMismatch",
            )

        try:
            await self._publisher.publish(event.to_payload())
        except CloudflareQueueTransientError as exc:
            if isinstance(exc, CloudflareQueueDisabledError):
                return OutboxHandlerResult.paused(
                    str(exc), error_type=type(exc).__name__
                )
            return OutboxHandlerResult.transient_failure(
                str(exc), error_type=type(exc).__name__
            )
        except (
            CloudflareQueueConfigurationError,
            CloudflareQueuePermanentError,
        ) as exc:
            return OutboxHandlerResult.permanent_failure(
                str(exc), error_type=type(exc).__name__
            )
        except Exception as exc:
            return OutboxHandlerResult.transient_failure(
                "Unexpected integration event publisher failure",
                error_type=type(exc).__name__,
            )

        return OutboxHandlerResult.ok(
            metadata={
                "event_id": context.event_id,
                "destination": "cloudflare_queue",
                "queue": self._publisher.queue_name,
            }
        )
