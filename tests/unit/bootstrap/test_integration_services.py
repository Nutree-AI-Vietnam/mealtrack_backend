from types import SimpleNamespace

import pytest

from src.bootstrap.integration_services import (
    drain_integration_event_publisher,
    get_firebase_executor,
    get_integration_event_publisher,
    get_wearable_account_service,
    reset_integration_event_publisher_for_tests,
    shutdown_firebase_executor,
)
from src.infra.adapters.open_wearables_client import OpenWearablesClient


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


def _open_wearables_settings(monkeypatch, url: str, key: str) -> None:
    monkeypatch.setattr(
        "src.bootstrap.integration_services.get_settings",
        lambda: SimpleNamespace(
            OPEN_WEARABLES_API_URL=url,
            OPEN_WEARABLES_API_KEY=key,
        ),
    )


def test_wearable_account_service_needs_only_url_and_api_key(monkeypatch):
    _open_wearables_settings(monkeypatch, "https://wearables.example", "key")

    assert isinstance(get_wearable_account_service(), OpenWearablesClient)


@pytest.mark.parametrize(
    ("url", "key"),
    [("", ""), ("https://wearables.example", ""), ("", "key")],
)
def test_wearable_account_service_is_none_when_unconfigured(monkeypatch, url, key):
    _open_wearables_settings(monkeypatch, url, key)

    assert get_wearable_account_service() is None
