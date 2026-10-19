"""PYTHON-DJ: expected non-food scan rejections must not open a Sentry issue."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from src.api.exceptions import ValidationException
from src.app.commands.meal.scan_by_url_command import ScanByUrlCommand
from src.app.handlers.command_handlers.scan_by_url_command_handler import (
    ScanByUrlCommandHandler,
)
from src.infra.event_bus.pymediator_event_bus import PyMediatorEventBus
from src.infra.monitoring.sentry import (
    SentryObservabilityConnector,
    _drop_expected_events,
)
from src.observability import (
    reset_observability_connector_for_test,
    set_observability_connector_for_test,
)

_IMAGE_URL = "https://photos.example.test/mealtrack/abc"


class _CaptureTransport(sentry_sdk.transport.Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list = []

    def capture_envelope(self, envelope) -> None:
        self.envelopes.append(envelope)


def _events(transport: _CaptureTransport) -> list[dict]:
    events = []
    for envelope in transport.envelopes:
        event = envelope.get_event()
        if event is not None:
            events.append(event)
    return events


def _event_text(event: dict) -> str:
    message = event.get("message")
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        formatted = message.get("formatted") or message.get("message") or ""
        return str(formatted)
    logentry = event.get("logentry") or {}
    if isinstance(logentry, dict):
        return str(logentry.get("formatted") or logentry.get("message") or "")
    return ""


def _uow_with_timezone() -> MagicMock:
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)
    uow.users = MagicMock()
    uow.users.get_user_timezone = AsyncMock(return_value="UTC")
    uow.meals = MagicMock()
    uow.meals.save = AsyncMock()
    uow.commit = AsyncMock()
    return uow


def _handler(uow: MagicMock) -> ScanByUrlCommandHandler:
    handler = ScanByUrlCommandHandler(
        uow=uow,
        event_bus=MagicMock(),
        download_image_bytes=AsyncMock(return_value=b"fake-image-bytes"),
    )
    handler.vision_service = MagicMock()
    handler.gpt_parser = MagicMock()
    return handler


def _command() -> ScanByUrlCommand:
    return ScanByUrlCommand(
        user_id="00000000-0000-0000-0000-000000000001",
        image_url=_IMAGE_URL,
        public_id="mealtrack/abc",
    )


@pytest.fixture
def capturing_sentry():
    """Production-shaped client: ERROR logs are events, warnings are not."""
    transport = _CaptureTransport()
    sentry_sdk.init(
        dsn="https://public@example.ingest.sentry.io/1",
        transport=transport,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        before_send=_drop_expected_events,
        traces_sample_rate=0,
    )
    connector = SentryObservabilityConnector()
    connector._enabled = True
    set_observability_connector_for_test(connector)
    try:
        yield transport
    finally:
        sentry_sdk.flush(timeout=2)
        sentry_sdk.get_client().close(timeout=2)
        sentry_sdk.init(dsn="")
        reset_observability_connector_for_test()


@pytest.mark.asyncio
async def test_image_rejected_is_not_a_sentry_error_but_scan_failures_are(
    capturing_sentry,
):
    """Fails while meal_scan.image_rejected is sent with capture_message.

    PYTHON-DJ is the food guard on POST /v1/meals/scan-by-url: the vision
    model returns is_food=false and the handler rejects the image. That
    outcome stays a client validation error. Unexpected scan failures must
    still become Sentry error events.
    """
    uow = _uow_with_timezone()
    handler = _handler(uow)
    handler.vision_service.analyze = AsyncMock(
        return_value={"structured_data": {"is_food": False, "foods": []}}
    )
    handler.gpt_parser.parse_is_food.return_value = False

    bus = PyMediatorEventBus()
    bus.register_handler(type(_command()), handler)

    with pytest.raises(
        ValidationException, match="Image does not appear to contain food"
    ):
        await bus.send(_command())

    rejected_events = _events(capturing_sentry)
    assert not rejected_events, [
        {"level": event.get("level"), "message": _event_text(event)}
        for event in rejected_events
    ]
    uow.meals.save.assert_not_called()

    capturing_sentry.envelopes.clear()
    handler.vision_service.analyze = AsyncMock(
        side_effect=RuntimeError("vision provider unavailable")
    )
    with pytest.raises(RuntimeError, match="vision provider unavailable"):
        await bus.send(_command())

    failure_events = _events(capturing_sentry)
    assert any(
        event.get("level") == "error" and "ScanByUrlCommand" in _event_text(event)
        for event in failure_events
    ), [
        {"level": event.get("level"), "message": _event_text(event)}
        for event in failure_events
    ]
