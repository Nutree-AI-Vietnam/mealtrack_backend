"""Process-wide provider-neutral observability facade."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from contextvars import ContextVar
from typing import Any

from src.observability_connectors import (
    NoopObservabilityConnector,
    ObservabilityConnector,
)

_connector: ObservabilityConnector = NoopObservabilityConnector()
# Set by request middleware via set_request_context. Chat instrumentation reads
# it back; there is no other request-id handle on the orchestrator.
_request_id: ContextVar[str | None] = ContextVar("mealtrack_request_id", default=None)


def get_observability_connector() -> ObservabilityConnector:
    """Return the process-wide observability connector."""
    return _connector


def set_observability_connector(connector: ObservabilityConnector) -> None:
    """Install the process-wide observability connector."""
    global _connector
    _connector = connector


def set_observability_connector_for_test(
    connector: ObservabilityConnector | None,
) -> None:
    """Replace the process connector in tests."""
    set_observability_connector(connector or NoopObservabilityConnector())


def reset_observability_connector_for_test() -> None:
    """Reset the process connector in tests."""
    use_noop_observability()


def initialize_observability() -> None:
    """Initialize the configured observability provider."""
    get_observability_connector().initialize()


def capture_exception(
    error: BaseException,
    *,
    context: dict[str, Any] | None = None,
    fingerprint: Sequence[str] | None = None,
) -> None:
    """Capture an unexpected exception through the active connector.

    ``fingerprint`` pins the event to a fixed issue whatever its message or
    stack (its first part becomes the event's ``alert`` tag); omitted, the
    provider groups it as usual.
    """
    connector = get_observability_connector()
    if fingerprint is None:
        connector.capture_exception(error, context=context)
    else:
        connector.capture_exception(
            error, context=context, fingerprint=list(fingerprint)
        )


def capture_message(
    message: str,
    *,
    level: str = "info",
    context: dict[str, Any] | None = None,
    fingerprint: Sequence[str] | None = None,
) -> None:
    """Capture an operational message through the active connector."""
    connector = get_observability_connector()
    if fingerprint is None:
        connector.capture_message(message, level=level, context=context)
    else:
        connector.capture_message(
            message, level=level, context=context, fingerprint=list(fingerprint)
        )


def log_event(
    level: str,
    message: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Emit a structured operational log through the active connector."""
    get_observability_connector().log_event(
        level,
        message,
        attributes=attributes,
    )


def increment_metric(
    name: str,
    value: float = 1.0,
    *,
    unit: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Increment an operational counter metric."""
    get_observability_connector().increment_metric(
        name,
        value,
        unit=unit,
        attributes=attributes,
    )


def gauge_metric(
    name: str,
    value: float,
    *,
    unit: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Record an operational gauge metric."""
    get_observability_connector().gauge_metric(
        name,
        value,
        unit=unit,
        attributes=attributes,
    )


def distribution_metric(
    name: str,
    value: float,
    *,
    unit: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Record an operational distribution metric."""
    get_observability_connector().distribution_metric(
        name,
        value,
        unit=unit,
        attributes=attributes,
    )


def set_request_context(
    *,
    request_id: str,
    method: str,
    path: str,
    user_id: str | None = None,
) -> None:
    """Attach safe request context to later provider events."""
    _request_id.set(request_id or None)
    get_observability_connector().set_request_context(
        request_id=request_id,
        method=method,
        path=path,
        user_id=user_id,
    )


def current_request_id() -> str | None:
    """Return the request id bound by ``set_request_context``, if any."""
    request_id = _request_id.get()
    if not isinstance(request_id, str) or not request_id.strip():
        return None
    return request_id


def reset_request_id_for_test() -> None:
    """Clear the bound request id in tests."""
    _request_id.set(None)


def start_span(
    *,
    operation: str,
    description: str | None = None,
    context: dict[str, Any] | None = None,
) -> AbstractContextManager[Any]:
    """Start a provider span, or no-op when disabled."""
    return get_observability_connector().start_span(
        operation=operation,
        description=description,
        context=context,
    )


def flush_observability(*, timeout: float = 5) -> None:
    """Flush pending provider events before process exit."""
    get_observability_connector().flush(timeout=timeout)


def use_noop_observability() -> None:
    """Install the no-op connector."""
    set_observability_connector(NoopObservabilityConnector())
