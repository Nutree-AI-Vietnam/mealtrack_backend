"""Composition-root factories for external integration services."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from src.app.events.in_process_event_router import in_process_event_router
from src.app.services.meal_insight_recovery import MealInsightRecovery
from src.domain.ports.affiliate_service_port import AffiliateServicePort
from src.domain.ports.integration_event_publisher_port import (
    IntegrationEventPublisherPort,
)
from src.domain.ports.play_subscription_port import PlaySubscriptionPort
from src.infra.adapters.best_effort_integration_event_publisher import (
    BestEffortIntegrationEventPublisher,
)
from src.infra.adapters.cloudflare_queue_publisher import CloudflareQueuePublisher
from src.infra.adapters.google_play_subscription_adapter import (
    GooglePlaySubscriptionAdapter,
)
from src.infra.concurrency.executors import (
    get_firebase_executor as _get_firebase_executor,
)
from src.infra.concurrency.executors import (
    shutdown_firebase_executor as _shutdown_firebase_executor,
)
from src.infra.config.settings import get_settings
from src.infra.services.firebase_auth_service import FirebaseAuthService

logger = logging.getLogger(__name__)

_publisher: BestEffortIntegrationEventPublisher | None = None
_play_subscriptions: PlaySubscriptionPort | None = None

FIREBASE_DELETE_ATTEMPTS = 3
FIREBASE_DELETE_BACKOFF_SECONDS = 1.0


def _build_event_transport() -> IntegrationEventPublisherPort:
    """Queue to nutreeai_async, or the API process's own handlers once switched."""
    if get_settings().INTEGRATION_EVENT_TRANSPORT == "in_process":
        return in_process_event_router
    return CloudflareQueuePublisher.from_settings()


def get_integration_event_publisher() -> IntegrationEventPublisherPort:
    """Return the process-local event publisher; transport failures must not fail writes."""
    global _publisher
    if _publisher is None:
        _publisher = BestEffortIntegrationEventPublisher(_build_event_transport())
    return _publisher


def get_meal_insight_recovery() -> MealInsightRecovery:
    """Re-request overdue meal insights through the same event publisher."""
    return MealInsightRecovery(
        get_integration_event_publisher(), get_settings().ENVIRONMENT
    )


async def drain_integration_event_publisher() -> None:
    """Flush in-flight event publishes during process shutdown."""
    if _publisher is None:
        return
    await _publisher.drain()


def reset_integration_event_publisher_for_tests() -> None:
    """Drop the singleton so tests can rebuild a publisher."""
    global _publisher
    _publisher = None


async def delete_firebase_account(firebase_uid: str) -> None:
    """Delete a deleted user's Firebase Auth account, retrying transient failures.

    A UID that is already gone counts as deleted. After the last attempt the
    UID is logged at ERROR for ``scripts/delete_missed_firebase_accounts.py``.
    """
    loop = asyncio.get_running_loop()
    for attempt in range(1, FIREBASE_DELETE_ATTEMPTS + 1):
        try:
            await loop.run_in_executor(
                get_firebase_executor(),
                FirebaseAuthService.delete_firebase_user,
                firebase_uid,
            )
        except Exception:
            if attempt == FIREBASE_DELETE_ATTEMPTS:
                logger.error(
                    "Firebase account cleanup failed for firebase_uid=%s",
                    firebase_uid,
                    exc_info=True,
                )
                return
            await asyncio.sleep(FIREBASE_DELETE_BACKOFF_SECONDS * attempt)
        else:
            logger.info(
                "Firebase account cleanup completed for firebase_uid=%s",
                firebase_uid,
            )
            return


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


def get_play_subscription_service() -> PlaySubscriptionPort:
    """Return the process-local Google Play adapter; disabled unless configured."""
    global _play_subscriptions
    if _play_subscriptions is None:
        _play_subscriptions = GooglePlaySubscriptionAdapter.from_settings()
    return _play_subscriptions


def reset_play_subscription_service_for_tests() -> None:
    """Drop the singleton so tests can rebuild it from new settings."""
    global _play_subscriptions
    _play_subscriptions = None
