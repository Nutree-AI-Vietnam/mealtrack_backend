"""Tests for the Sentry observability connector."""

import asyncio
from contextlib import nullcontext
from unittest.mock import MagicMock

from src.infra.monitoring.connectors import (
    filter_safe_attributes,
    filter_safe_context,
    filter_safe_tags,
)
from src.infra.monitoring.sentry import (
    SentryObservabilityConnector,
    _drop_expected_chat_cancellation,
)


def test_filter_safe_context_drops_sensitive_and_complex_values():
    safe = filter_safe_context(
        {
            "request_id": "abc12345",
            "method": "POST",
            "path": "/v1/meals",
            "user_id": "user-1",
            "authorization": "Bearer secret",
            "firebase_claims": {"email": "person@example.com"},
            "food_payload": {"name": "rice"},
            "raw_image_url": "https://example.com/image.jpg",
            "event_id": "evt-1",
            "payload": {"secret": "value"},
        }
    )

    assert safe == {
        "request_id": "abc12345",
        "method": "POST",
        "path": "/v1/meals",
        "user_id": "user-1",
        "event_id": "evt-1",
    }


def test_filter_safe_tags_uses_restricted_tag_keys():
    tags = filter_safe_tags(
        {
            "request_id": "abc12345",
            "user_id": "user-1",
            "event_id": "evt-1",
            "component": "cron",
        }
    )

    assert tags == {"request_id": "abc12345", "component": "cron"}


def test_filter_safe_attributes_drops_sensitive_none_and_complex_values():
    attributes = filter_safe_attributes(
        {
            "request_id": "abc12345",
            "component": "cron",
            "operation": "dispatch",
            "status": "ok",
            "authorization": "Bearer secret",
            "firebase_claims": {"email": "person@example.com"},
            "food_payload": {"name": "rice"},
            "payload": {"secret": "value"},
            "email": "person@example.com",
            "elapsed_ms": 12.5,
            "release": None,
        }
    )

    assert attributes == {
        "request_id": "abc12345",
        "component": "cron",
        "operation": "dispatch",
        "status": "ok",
        "elapsed_ms": 12.5,
    }


def test_initialize_skips_when_dsn_unset(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", None)

    connector = SentryObservabilityConnector()
    connector.initialize()

    mock_sdk.init.assert_not_called()


def test_initialize_configures_integrations_and_release(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.ENVIRONMENT", "test")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_RELEASE", "rel-1")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_SEND_PII", False)
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_ENABLE_LOGS", True)
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.SENTRY_ENABLE_METRICS", True
    )
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.SENTRY_PROFILE_SESSION_SAMPLE_RATE",
        0.02,
    )
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.SENTRY_PROFILE_LIFECYCLE",
        "trace",
    )

    connector = SentryObservabilityConnector()
    connector.initialize()

    init_kwargs = mock_sdk.init.call_args.kwargs
    assert init_kwargs["dsn"] == "dsn"
    assert init_kwargs["environment"] == "test"
    assert init_kwargs["release"] == "rel-1"
    assert init_kwargs["send_default_pii"] is False
    assert init_kwargs["enable_logs"] is True
    assert init_kwargs["enable_metrics"] is True
    assert init_kwargs["profile_session_sample_rate"] == 0.02
    assert init_kwargs["profile_lifecycle"] == "trace"
    cancellation = asyncio.CancelledError("Cancelled via cancel scope")
    assert len(init_kwargs["integrations"]) == 4
    starlette, fastapi, *_ = init_kwargs["integrations"]
    assert starlette.failed_request_status_codes == {500, 501, 502, 504}
    assert fastapi.failed_request_status_codes == {500, 501, 502, 504}
    assert (
        init_kwargs["before_send"](
            {
                "transaction": "/v1/chat/messages",
                "exception": {
                    "values": [
                        {
                            "type": "CancelledError",
                            "value": "Cancelled via cancel scope",
                        }
                    ]
                },
            },
            {"exc_info": (asyncio.CancelledError, cancellation, None)},
        )
        is None
    )


def test_before_send_keeps_non_chat_and_non_cancellation_errors():
    cancellation_event = {
        "transaction": "/v1/chat/messages",
        "exception": {"values": [{"type": "CancelledError"}]},
    }
    provider_error = {
        "transaction": "/v1/chat/messages",
        "exception": {"values": [{"type": "RuntimeError", "value": "boom"}]},
    }
    other_route = {**cancellation_event, "transaction": "/v1/meals/scan-by-url"}
    cancellation = asyncio.CancelledError("cancelled")
    hint = {"exc_info": (asyncio.CancelledError, cancellation, None)}

    assert (
        _drop_expected_chat_cancellation(
            provider_error,
            {"exc_info": (RuntimeError, RuntimeError("boom"), None)},
        )
        is provider_error
    )
    assert _drop_expected_chat_cancellation(other_route, hint) is other_route


def test_before_send_keeps_chained_provider_failure_and_unverifiable_events():
    provider_error = RuntimeError("provider failed")
    cancellation = asyncio.CancelledError("cancelled")
    cancellation.__cause__ = provider_error
    event = {
        "transaction": "/v1/chat/messages",
        "exception": {"values": [{"type": "CancelledError"}]},
    }

    assert (
        _drop_expected_chat_cancellation(
            event,
            {"exc_info": (asyncio.CancelledError, cancellation, None)},
        )
        is event
    )
    assert _drop_expected_chat_cancellation(event, {}) is event


def test_initialize_omits_optional_profile_session_settings_when_unset(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.SENTRY_PROFILE_SESSION_SAMPLE_RATE",
        None,
    )
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.SENTRY_PROFILE_LIFECYCLE",
        None,
    )

    connector = SentryObservabilityConnector()
    connector.initialize()

    init_kwargs = mock_sdk.init.call_args.kwargs
    assert "profile_session_sample_rate" not in init_kwargs
    assert "profile_lifecycle" not in init_kwargs


def test_capture_message_applies_only_safe_context(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)

    connector = SentryObservabilityConnector()
    connector._enabled = True
    connector.capture_message(
        "Affiliate outbox row permanently failed",
        level="error",
        context={
            "component": "affiliate_outbox",
            "row_id": "row-1",
            "event_id": "evt-1",
            "event_type": "signup",
            "payload": {"email": "person@example.com"},
        },
    )

    mock_sdk.capture_message.assert_called_once_with(
        "Affiliate outbox row permanently failed",
        level="error",
    )
    safe_context = mock_sdk.set_context.call_args.args[1]
    assert safe_context == {
        "component": "affiliate_outbox",
        "row_id": "row-1",
        "event_id": "evt-1",
        "event_type": "signup",
    }
    assert "payload" not in safe_context


def test_request_context_sets_safe_context_and_user(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.ENVIRONMENT", "test")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_RELEASE", "rel-1")

    connector = SentryObservabilityConnector()
    connector._enabled = True
    connector.set_request_context(
        request_id="abc12345",
        method="GET",
        path="/v1/meals",
        user_id="user-1",
    )

    safe_context = mock_sdk.set_context.call_args.args[1]
    assert safe_context["request_id"] == "abc12345"
    assert safe_context["method"] == "GET"
    assert safe_context["path"] == "/v1/meals"
    assert safe_context["environment"] == "test"
    assert safe_context["release"] == "rel-1"
    mock_sdk.set_user.assert_called_once_with({"id": "user-1"})


def test_start_span_returns_noop_when_disabled(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)

    connector = SentryObservabilityConnector()
    span = connector.start_span(operation="cron.email")

    assert type(span) is type(nullcontext())
    mock_sdk.start_span.assert_not_called()


def test_log_event_routes_to_sentry_logger_with_safe_attributes(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)

    connector = SentryObservabilityConnector()
    connector._enabled = True
    connector.log_event(
        "warning",
        "cron phase failed",
        attributes={
            "component": "cron.push",
            "operation": "dispatch",
            "status": "failed",
            "payload": {"email": "person@example.com"},
        },
    )

    mock_sdk.logger.warning.assert_called_once_with(
        "cron phase failed",
        attributes={
            "component": "cron.push",
            "operation": "dispatch",
            "status": "failed",
        },
    )


def test_log_event_normalizes_unknown_level(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)

    connector = SentryObservabilityConnector()
    connector._enabled = True
    connector.log_event("notice", "hello")

    mock_sdk.logger.info.assert_called_once_with("hello", attributes={})


def test_log_event_maps_critical_to_sentry_fatal(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)

    connector = SentryObservabilityConnector()
    connector._enabled = True
    connector.log_event("critical", "database unavailable")

    mock_sdk.logger.fatal.assert_called_once_with(
        "database unavailable",
        attributes={},
    )
    mock_sdk.logger.critical.assert_not_called()


def test_log_event_noops_when_disabled(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)

    connector = SentryObservabilityConnector()
    connector.log_event("info", "hello")

    mock_sdk.logger.info.assert_not_called()


def test_metrics_route_to_sentry_metrics_with_safe_attributes(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)

    connector = SentryObservabilityConnector()
    connector._enabled = True
    attrs = {
        "component": "api",
        "route": "/v1/meals",
        "status": "ok",
        "authorization": "Bearer secret",
    }

    connector.increment_metric("api.request.count", attributes=attrs)
    connector.gauge_metric("db.pool.size", 3, unit="connection", attributes=attrs)
    connector.distribution_metric(
        "api.request.elapsed",
        25.5,
        unit="millisecond",
        attributes=attrs,
    )

    safe_attrs = {"component": "api", "route": "/v1/meals", "status": "ok"}
    mock_sdk.metrics.count.assert_called_once_with(
        "api.request.count",
        1.0,
        unit=None,
        attributes=safe_attrs,
    )
    mock_sdk.metrics.gauge.assert_called_once_with(
        "db.pool.size",
        3,
        unit="connection",
        attributes=safe_attrs,
    )
    mock_sdk.metrics.distribution.assert_called_once_with(
        "api.request.elapsed",
        25.5,
        unit="millisecond",
        attributes=safe_attrs,
    )
