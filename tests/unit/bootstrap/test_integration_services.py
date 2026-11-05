from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bootstrap.integration_services import (
    drain_integration_event_publisher,
    get_firebase_executor,
    get_integration_event_publisher,
    reset_integration_event_publisher_for_tests,
    shutdown_firebase_executor,
)


def _use_transport(monkeypatch, transport: str) -> dict[str, object]:
    """Stub both transports and select one; returns the built inners by name."""
    inners = {"cloudflare_queue": object(), "redis_stream": AsyncMock()}
    monkeypatch.setattr(
        "src.bootstrap.integration_services.get_settings",
        lambda: SimpleNamespace(INTEGRATION_EVENT_TRANSPORT=transport),
    )
    monkeypatch.setattr(
        "src.bootstrap.integration_services.CloudflareQueuePublisher",
        SimpleNamespace(from_settings=lambda: inners["cloudflare_queue"]),
    )
    monkeypatch.setattr(
        "src.bootstrap.integration_services.RedisStreamEventPublisher",
        SimpleNamespace(from_settings=lambda: inners["redis_stream"]),
    )
    return inners


def test_integration_event_publisher_is_process_singleton(monkeypatch):
    reset_integration_event_publisher_for_tests()
    inners = _use_transport(monkeypatch, "cloudflare_queue")

    first = get_integration_event_publisher()
    second = get_integration_event_publisher()

    assert first is second
    assert first._inner is inners["cloudflare_queue"]
    reset_integration_event_publisher_for_tests()


def test_redis_stream_transport_replaces_the_cloudflare_queue(monkeypatch):
    reset_integration_event_publisher_for_tests()
    inners = _use_transport(monkeypatch, "redis_stream")

    publisher = get_integration_event_publisher()

    assert publisher._inner is inners["redis_stream"]
    reset_integration_event_publisher_for_tests()


@pytest.mark.asyncio
async def test_drain_closes_the_transport_after_in_flight_publishes(monkeypatch):
    reset_integration_event_publisher_for_tests()
    inners = _use_transport(monkeypatch, "redis_stream")
    stream = inners["redis_stream"]
    publisher = get_integration_event_publisher()
    await publisher.publish({"event_type": "meal.created.v1"})

    await drain_integration_event_publisher()

    stream.publish.assert_awaited_once_with({"event_type": "meal.created.v1"})
    stream.aclose.assert_awaited_once()
    reset_integration_event_publisher_for_tests()


@pytest.mark.asyncio
async def test_drain_is_noop_without_publisher():
    reset_integration_event_publisher_for_tests()
    await drain_integration_event_publisher()


def test_firebase_executor_accessors_delegate_to_infra(monkeypatch):
    sentinel = object()
    called = {"shutdown": 0}
    monkeypatch.setattr(
        "src.bootstrap.integration_services._get_firebase_executor",
        lambda: sentinel,
    )
    monkeypatch.setattr(
        "src.bootstrap.integration_services._shutdown_firebase_executor",
        lambda: called.__setitem__("shutdown", called["shutdown"] + 1),
    )

    assert get_firebase_executor() is sentinel
    shutdown_firebase_executor()
    assert called["shutdown"] == 1


def test_play_subscription_service_is_built_once_from_settings(monkeypatch):
    from src.bootstrap.integration_services import (
        get_play_subscription_service,
        reset_play_subscription_service_for_tests,
    )

    reset_play_subscription_service_for_tests()
    built = []
    monkeypatch.setattr(
        "src.bootstrap.integration_services.GooglePlaySubscriptionAdapter",
        SimpleNamespace(from_settings=lambda: built.append(object()) or built[-1]),
    )

    first = get_play_subscription_service()

    assert get_play_subscription_service() is first is built[0]
    assert len(built) == 1
    reset_play_subscription_service_for_tests()
