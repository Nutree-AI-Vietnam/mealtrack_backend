from types import SimpleNamespace

import pytest

from src.bootstrap.integration_services import (
    drain_integration_event_publisher,
    get_firebase_executor,
    get_integration_event_publisher,
    reset_integration_event_publisher_for_tests,
    shutdown_firebase_executor,
)


def test_integration_event_publisher_is_process_singleton(monkeypatch):
    reset_integration_event_publisher_for_tests()
    inner = object()
    monkeypatch.setattr(
        "src.bootstrap.integration_services.CloudflareQueuePublisher",
        SimpleNamespace(from_settings=lambda: inner),
    )

    first = get_integration_event_publisher()
    second = get_integration_event_publisher()

    assert first is second
    assert first._inner is inner
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
