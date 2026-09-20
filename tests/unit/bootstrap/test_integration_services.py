from types import SimpleNamespace

import pytest

from src.bootstrap.integration_services import (
    drain_integration_event_publisher,
    get_integration_event_publisher,
    reset_integration_event_publisher_for_tests,
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
