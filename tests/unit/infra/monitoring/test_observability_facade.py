"""Tests for the provider-neutral observability facade."""

from contextlib import nullcontext

from src.observability import (
    capture_exception,
    capture_message,
    distribution_metric,
    flush_observability,
    gauge_metric,
    get_observability_connector,
    increment_metric,
    initialize_observability,
    log_event,
    reset_observability_connector_for_test,
    set_observability_connector_for_test,
    set_request_context,
    start_span,
)
from src.observability_connectors import filter_safe_attributes, filter_safe_tags


class RecordingConnector:
    def __init__(self):
        self.calls = []

    def initialize(self):
        self.calls.append(("initialize",))

    def capture_exception(self, error, *, context=None):
        self.calls.append(("capture_exception", error, context))

    def capture_message(self, message, *, level="info", context=None):
        self.calls.append(("capture_message", message, level, context))

    def log_event(self, level, message, *, attributes=None):
        self.calls.append(("log_event", level, message, attributes))

    def increment_metric(self, name, value=1.0, *, unit=None, attributes=None):
        self.calls.append(("increment_metric", name, value, unit, attributes))

    def gauge_metric(self, name, value, *, unit=None, attributes=None):
        self.calls.append(("gauge_metric", name, value, unit, attributes))

    def distribution_metric(self, name, value, *, unit=None, attributes=None):
        self.calls.append(("distribution_metric", name, value, unit, attributes))

    def set_request_context(self, *, request_id, method, path, user_id=None):
        self.calls.append(("set_request_context", request_id, method, path, user_id))

    def start_span(self, *, operation, description=None, context=None):
        self.calls.append(("start_span", operation, description, context))
        return nullcontext()

    def flush(self, *, timeout=5):
        self.calls.append(("flush", timeout))


def teardown_function():
    reset_observability_connector_for_test()


def test_facade_delegates_to_configured_connector():
    connector = RecordingConnector()
    set_observability_connector_for_test(connector)
    error = RuntimeError("boom")

    initialize_observability()
    capture_exception(error, context={"component": "cron"})
    capture_message("hello", level="error", context={"operation": "send"})
    log_event("info", "cron started", attributes={"component": "cron"})
    increment_metric("cron.started", attributes={"component": "cron"})
    gauge_metric("db.pool.size", 3, unit="connection", attributes={"component": "db"})
    distribution_metric(
        "request.elapsed",
        25.5,
        unit="millisecond",
        attributes={"route": "/v1/meals"},
    )
    set_request_context(request_id="abc12345", method="GET", path="/v1/meals")
    with start_span(operation="cron.email", description="email"):
        pass
    flush_observability(timeout=2)

    assert connector.calls == [
        ("initialize",),
        ("capture_exception", error, {"component": "cron"}),
        ("capture_message", "hello", "error", {"operation": "send"}),
        ("log_event", "info", "cron started", {"component": "cron"}),
        ("increment_metric", "cron.started", 1.0, None, {"component": "cron"}),
        ("gauge_metric", "db.pool.size", 3, "connection", {"component": "db"}),
        (
            "distribution_metric",
            "request.elapsed",
            25.5,
            "millisecond",
            {"route": "/v1/meals"},
        ),
        ("set_request_context", "abc12345", "GET", "/v1/meals", None),
        ("start_span", "cron.email", "email", None),
        ("flush", 2),
    ]


def test_facade_forwards_a_fingerprint_only_when_one_is_given():
    """Connectors written before fingerprints keep working unchanged."""

    class FingerprintConnector(RecordingConnector):
        def capture_exception(self, error, *, context=None, fingerprint=None):
            self.calls.append(("capture_exception", error, context, fingerprint))

        def capture_message(
            self, message, *, level="info", context=None, fingerprint=None
        ):
            self.calls.append(("capture_message", message, level, fingerprint))

    legacy = RecordingConnector()
    set_observability_connector_for_test(legacy)
    error = RuntimeError("boom")
    capture_exception(error, context={"component": "cron"})
    capture_message("hello")
    assert legacy.calls == [
        ("capture_exception", error, {"component": "cron"}),
        ("capture_message", "hello", "info", None),
    ]

    connector = FingerprintConnector()
    set_observability_connector_for_test(connector)
    capture_exception(error, fingerprint=("alert.key", "kind"))
    capture_message("hello", level="warning", fingerprint=["alert.key"])
    assert connector.calls == [
        ("capture_exception", error, None, ["alert.key", "kind"]),
        ("capture_message", "hello", "warning", ["alert.key"]),
    ]


def test_cache_hit_is_safe_observability_attribute_and_tag():
    attributes = {
        "ai_provider": "openai",
        "ai_model": "gpt-5.4-mini-2026-03-17",
        "ai_purpose": "parse_text",
        "cache_hit": "true",
        "user_prompt": "do not export",
    }

    assert filter_safe_attributes(attributes) == {
        "ai_provider": "openai",
        "ai_model": "gpt-5.4-mini-2026-03-17",
        "ai_purpose": "parse_text",
        "cache_hit": "true",
    }
    assert filter_safe_tags(attributes)["cache_hit"] == "true"


def test_food_search_source_and_language_are_safe_metric_tags():
    attributes = {
        "operation": "search",
        "source": "local",
        "language": "vi",
        "status": "success",
        "query": "do not export",
    }

    assert filter_safe_attributes(attributes) == {
        "operation": "search",
        "source": "local",
        "language": "vi",
        "status": "success",
    }
    assert filter_safe_tags(attributes) == {
        "operation": "search",
        "source": "local",
        "language": "vi",
        "status": "success",
    }


def test_meal_scan_rejection_image_context_is_safe_but_not_tagged():
    attributes = {
        "component": "meal_scan",
        "operation": "scan_by_url",
        "ai_purpose": "meal_scan",
        "image_id": "img-123",
        "image_url": "https://res.cloudinary.com/test/image/upload/v1/mealtrack/img-123.jpg",
        "rejection_reason": "parser_not_food",
        "raw_image_bytes": b"do not export",
    }

    safe_attributes = filter_safe_attributes(attributes)

    assert safe_attributes == {
        "component": "meal_scan",
        "operation": "scan_by_url",
        "ai_purpose": "meal_scan",
        "image_id": "img-123",
        "image_url": "https://res.cloudinary.com/test/image/upload/v1/mealtrack/img-123.jpg",
        "rejection_reason": "parser_not_food",
    }
    assert "image_url" not in filter_safe_tags(attributes)


def test_noop_connector_methods_do_not_raise():
    from src.infra.monitoring.connectors import NoopObservabilityConnector

    connector = NoopObservabilityConnector()
    connector.initialize()
    connector.capture_exception(RuntimeError("boom"))
    connector.capture_exception(RuntimeError("boom"), fingerprint=["alert.key"])
    connector.capture_message("hello")
    connector.capture_message("hello", fingerprint=["alert.key"])
    connector.log_event("info", "hello")
    connector.increment_metric("cron.started")
    connector.gauge_metric("db.pool.size", 3)
    connector.distribution_metric("request.elapsed", 25.5)
    connector.set_request_context(request_id="abc12345", method="GET", path="/")
    with connector.start_span(operation="noop"):
        pass
    connector.flush(timeout=1)


def test_facade_defaults_to_noop_connector():
    initialize_observability()


def test_bootstrap_installs_infrastructure_connector(monkeypatch):
    from src.bootstrap import observability as bootstrap_observability
    from src.infra.monitoring import sentry as sentry_module

    connector = RecordingConnector()
    monkeypatch.setattr(
        sentry_module,
        "SentryObservabilityConnector",
        lambda: connector,
    )

    bootstrap_observability.initialize_observability()

    assert get_observability_connector() is connector
    assert connector.calls == [("initialize",)]
