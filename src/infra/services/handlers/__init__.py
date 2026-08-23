"""Outbox event handlers package and default registry factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.app.events.integration_event import IntegrationEvent
from src.infra.adapters.cloudflare_queue_publisher import CloudflareQueuePublisher
from src.infra.services.handlers.affiliate_webhook_handler import (
    AffiliateWebhookHandler,
)
from src.infra.services.handlers.cache_invalidation_queue_handler import (
    CacheInvalidationQueueHandler,
)
from src.infra.services.handlers.firebase_account_cleanup_handler import (
    FirebaseAccountCleanupHandler,
)
from src.infra.services.handlers.integration_event_queue_handler import (
    IntegrationEventQueueHandler,
)
from src.infra.services.handlers.notification_reschedule_handler import (
    NotificationRescheduleHandler,
)
from src.infra.services.handlers.push_notification_queue_handler import (
    PushNotificationQueueHandler,
)
from src.infra.services.handlers.telemetry_handler import TelemetryHandler
from src.infra.services.outbox_handler_registry import OutboxHandlerRegistry

if TYPE_CHECKING:
    from src.domain.ports.affiliate_service_port import AffiliateServicePort
    from src.infra.adapters.posthog_adapter import PostHogAdapter

__all__ = [
    "AffiliateWebhookHandler",
    "FirebaseAccountCleanupHandler",
    "NotificationRescheduleHandler",
    "PushNotificationQueueHandler",
    "TelemetryHandler",
    "CacheInvalidationQueueHandler",
    "IntegrationEventQueueHandler",
    "create_default_handler_registry",
]


def create_default_handler_registry(
    *,
    affiliate_adapter: AffiliateServicePort | None = None,
    posthog_adapter: PostHogAdapter | None = None,
    cache_invalidation_publisher: CloudflareQueuePublisher | None = None,
    push_notification_publisher: CloudflareQueuePublisher | None = None,
    integration_event_publisher: CloudflareQueuePublisher | None = None,
) -> OutboxHandlerRegistry:
    """Create and configure an OutboxHandlerRegistry with built-in handlers."""
    registry = OutboxHandlerRegistry()

    affiliate_handler = AffiliateWebhookHandler(affiliate_adapter)
    push_queue_publisher = (
        push_notification_publisher or CloudflareQueuePublisher.from_settings()
    )
    active_push_handler = PushNotificationQueueHandler(push_queue_publisher)
    telemetry_handler = TelemetryHandler(posthog_adapter)
    firebase_cleanup_handler = FirebaseAccountCleanupHandler()
    notification_reschedule_handler = NotificationRescheduleHandler()
    cache_invalidation_handler = CacheInvalidationQueueHandler(
        cache_invalidation_publisher or CloudflareQueuePublisher.from_settings()
    )
    integration_handler = IntegrationEventQueueHandler(
        integration_event_publisher or CloudflareQueuePublisher.from_settings(),
        event_model=IntegrationEvent,
    )

    # Affiliate event routes
    for event_type in (
        "affiliate_event",
        "affiliate_webhook",
        "affiliate.referral_created",
        "affiliate.conversion",
    ):
        registry.register(event_type, affiliate_handler)

    # Push notification routes (Cloudflare Queue)
    for event_type in (
        "push_notification",
        "notification.push",
        "scheduled_push",
        "push_notification.v1",
    ):
        registry.register(event_type, active_push_handler)

    # Telemetry and analytics routes
    for event_type in (
        "telemetry_event",
        "analytics.event",
        "posthog.capture",
    ):
        registry.register(event_type, telemetry_handler)

    active_cleanup_handler = (
        PushNotificationQueueHandler(push_notification_publisher)
        if push_notification_publisher is not None
        else firebase_cleanup_handler
    )
    registry.register("firebase_account_cleanup", active_cleanup_handler)
    registry.register("user.account_cleanup.v1", active_cleanup_handler)

    registry.register("notification_reschedule", notification_reschedule_handler)
    registry.register("cache_invalidation.v1", cache_invalidation_handler)
    registry.register("hydration.created.v1", integration_handler)

    return registry
