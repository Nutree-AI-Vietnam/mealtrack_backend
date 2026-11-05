from types import SimpleNamespace

import pytest

from src.app.events.in_process_event_router import in_process_event_router
from src.bootstrap import integration_services
from src.bootstrap.integration_services import (
    delete_firebase_account,
    drain_integration_event_publisher,
    get_firebase_executor,
    get_integration_event_publisher,
    reset_integration_event_publisher_for_tests,
    shutdown_firebase_executor,
)


def _use_transport(monkeypatch, transport: str) -> object:
    """Select a transport and stub the Cloudflare one; returns its stand-in."""
    queue = object()
    monkeypatch.setattr(
        "src.bootstrap.integration_services.get_settings",
        lambda: SimpleNamespace(INTEGRATION_EVENT_TRANSPORT=transport),
    )
    monkeypatch.setattr(
        "src.bootstrap.integration_services.CloudflareQueuePublisher",
        SimpleNamespace(from_settings=lambda: queue),
    )
    return queue


def test_integration_event_publisher_is_process_singleton(monkeypatch):
    reset_integration_event_publisher_for_tests()
    queue = _use_transport(monkeypatch, "cloudflare_queue")

    first = get_integration_event_publisher()
    second = get_integration_event_publisher()

    assert first is second
    assert first._inner is queue
    reset_integration_event_publisher_for_tests()


def test_in_process_transport_hands_events_to_the_api_process(monkeypatch):
    reset_integration_event_publisher_for_tests()
    _use_transport(monkeypatch, "in_process")

    publisher = get_integration_event_publisher()

    assert publisher._inner is in_process_event_router
    reset_integration_event_publisher_for_tests()


@pytest.mark.asyncio
async def test_drain_is_noop_without_publisher():
    reset_integration_event_publisher_for_tests()
    await drain_integration_event_publisher()


def _firebase_deletes(monkeypatch, outcomes: list[object]) -> list[str]:
    """Replace the Firebase call with scripted outcomes; returns the UIDs sent."""
    calls: list[str] = []

    def delete_firebase_user(uid: str) -> bool:
        calls.append(uid)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return True

    monkeypatch.setattr(
        integration_services.FirebaseAuthService,
        "delete_firebase_user",
        staticmethod(delete_firebase_user),
    )
    monkeypatch.setattr(integration_services, "FIREBASE_DELETE_BACKOFF_SECONDS", 0)
    return calls


@pytest.mark.asyncio
async def test_delete_firebase_account_retries_a_transient_failure(monkeypatch):
    calls = _firebase_deletes(monkeypatch, [RuntimeError("503"), True])

    await delete_firebase_account("uid-1")

    assert calls == ["uid-1", "uid-1"]


@pytest.mark.asyncio
async def test_delete_firebase_account_logs_the_uid_after_the_last_attempt(
    monkeypatch, caplog
):
    calls = _firebase_deletes(monkeypatch, [RuntimeError("down")] * 3)

    with caplog.at_level("ERROR", logger="src.bootstrap.integration_services"):
        await delete_firebase_account("uid-2")

    assert calls == ["uid-2"] * 3
    assert "Firebase account cleanup failed for firebase_uid=uid-2" in caplog.text


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
