"""Composition-root factories for external integration services."""

from src.domain.ports.affiliate_service_port import AffiliateServicePort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.infra.adapters.cloudflare_queue_publisher import CloudflareQueuePublisher
from src.infra.config.settings import get_settings


def get_integration_event_publisher() -> IntegrationEventPublisherPort:
    """Build the required environment-specific integration-event publisher."""
    return CloudflareQueuePublisher.from_settings()


def get_affiliate_service(
    *, enabled: bool | None = None
) -> AffiliateServicePort | None:
    """Build the optional affiliate adapter when the integration is enabled."""
    settings = get_settings()
    if enabled is None:
        enabled = settings.AFFILIATE_INTEGRATION_ENABLED
    if not enabled:
        return None

    from src.infra.adapters.affiliate_service_adapter import AffiliateServiceAdapter

    return AffiliateServiceAdapter()
