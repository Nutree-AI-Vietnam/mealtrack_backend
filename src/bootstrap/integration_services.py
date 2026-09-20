"""Composition-root factories for external integration services."""

from concurrent.futures import ThreadPoolExecutor

from src.domain.ports.affiliate_service_port import AffiliateServicePort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.infra.adapters.best_effort_integration_event_publisher import (
    BestEffortIntegrationEventPublisher,
)
from src.infra.adapters.cloudflare_queue_publisher import CloudflareQueuePublisher
from src.infra.concurrency.executors import (
    get_firebase_executor as _get_firebase_executor,
)
from src.infra.concurrency.executors import (
    shutdown_firebase_executor as _shutdown_firebase_executor,
)
from src.infra.config.settings import get_settings

_publisher: BestEffortIntegrationEventPublisher | None = None


def get_integration_event_publisher() -> IntegrationEventPublisherPort:
    """Return the process-local Queue publisher; transport failures must not fail writes."""
    global _publisher
    if _publisher is None:
        _publisher = BestEffortIntegrationEventPublisher(
            CloudflareQueuePublisher.from_settings()
        )
    return _publisher


async def drain_integration_event_publisher() -> None:
    """Flush in-flight Queue publishes during process shutdown."""
    if _publisher is None:
        return
    await _publisher.drain()


def reset_integration_event_publisher_for_tests() -> None:
    """Drop the singleton so tests can rebuild a publisher."""
    global _publisher
    _publisher = None


def get_firebase_executor() -> ThreadPoolExecutor:
    """Return the process-local Firebase Admin verification pool."""
    return _get_firebase_executor()


def shutdown_firebase_executor() -> None:
    """Stop the Firebase pool during process shutdown."""
    _shutdown_firebase_executor()


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
