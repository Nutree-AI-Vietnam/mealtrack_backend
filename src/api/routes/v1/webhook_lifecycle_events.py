"""Subscription lifecycle events for the affiliate service and Cloudflare Queues.

Each RevenueCat store event is reported as ``subscription.lifecycle.v1`` with
the provider event id mapped to a stable UUID, so a redelivered webhook
carries the same id downstream.
"""

import logging
import os
import uuid
from typing import Any

from src.app.events.affiliate.affiliate_events import SubscriptionLifecycleEvent
from src.domain.ports.integration_event_publisher_port import require_event_publisher

logger = logging.getLogger(__name__)


async def publish_subscription_event(
    event_publisher: Any,
    event_type: str,
    payload: dict,
    event_id: str | None,
    user_id: str,
) -> None:
    """Publish a subscription lifecycle event to Cloudflare Queues."""
    event = SubscriptionLifecycleEvent(
        event_id=subscription_event_id(event_id),
        environment=os.getenv("ENVIRONMENT", "development"),
        aggregate_id=user_id,
        data={"lifecycle_type": event_type, **payload},
    )
    await event_publisher.publish(event.to_payload())


def subscription_event_id(event_id: str | None) -> str:
    """Map the provider event identifier to the UUID required on the wire."""
    if not event_id:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(event_id))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"revenuecat:{event_id}"))


async def notify_affiliate(
    affiliate_handler: Any,
    event_type: str,
    payload: dict,
    event_id: str | None,
    event_publisher: Any = None,
    user_id: str | None = None,
) -> None:
    """Publish subscription event to Cloudflare Queue or send affiliate notification."""
    if user_id is not None:
        await publish_subscription_event(
            require_event_publisher(event_publisher),
            event_type,
            payload,
            event_id,
            user_id,
        )
    elif affiliate_handler is not None:
        resolved_event_id = event_id or str(uuid.uuid4())
        affiliate_payload = {
            **payload,
            "event_type": event_type,
            "event_id": resolved_event_id,
        }
        try:
            await affiliate_handler.send_event(affiliate_payload)
        except Exception as exc:
            logger.warning("Failed to send affiliate event: %s", exc)
