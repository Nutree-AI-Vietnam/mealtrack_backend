"""Sentry observability connector for the MealTrack API."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Literal, cast

try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
except ModuleNotFoundError:  # pragma: no cover
    sentry_sdk = None  # type: ignore[assignment]

from src.domain.exceptions.ai_exceptions import (
    CONTENT_REJECTION_ERROR_CODES,
    AIContentRejectedError,
)
from src.infra.config.settings import settings
from src.infra.monitoring.connectors import (
    filter_safe_attributes,
    filter_safe_context,
    filter_safe_tags,
)

logger = logging.getLogger(__name__)

# Opt out only when ENVIRONMENT is explicitly one of these. An unset or blank
# variable must still send when a DSN is set, so a missing env var cannot
# silence production (settings defaults ENVIRONMENT to "development").
_SENTRY_NON_REPORTING_ENVIRONMENTS = frozenset({"development", "local"})

SentryMessageLevel = Literal["fatal", "critical", "error", "warning", "info", "debug"]
SentryLogLevel = Literal["fatal", "error", "warning", "info", "debug", "trace"]
_SENTRY_MESSAGE_LEVELS = {"fatal", "critical", "error", "warning", "info", "debug"}
_SENTRY_LOG_LEVELS = {"fatal", "error", "warning", "info", "debug", "trace"}
_CHAT_MESSAGE_TRANSACTIONS = frozenset(
    {
        "/v1/chat/messages",
        "/v1/chat/threads/{thread_id}/messages",
    }
)
_LANGCHAIN_ORIGIN = "auto.ai.langchain"
_CONNECTION_TERMINATE_MESSAGE = "Exception terminating connection"


class SentryObservabilityConnector:
    """Sentry-backed observability connector.

    The rest of the codebase talks to ``observability.py`` so provider SDK
    details stay isolated here.
    """

    def __init__(self) -> None:
        self._enabled = False

    def initialize(self) -> None:
        """Initialize Sentry when this process should publish events.

        Sending is skipped only when ENVIRONMENT is explicitly development or
        local. An unset or blank ENVIRONMENT still sends when SENTRY_DSN is
        set. The environment label is SENTRY_ENVIRONMENT, then ENVIRONMENT.
        The release is SENTRY_RELEASE, then GIT_SHA, then settings.SENTRY_RELEASE.

        Must be called BEFORE the FastAPI app is instantiated so integrations
        can patch Starlette/FastAPI middleware.
        """
        explicit_environment = _explicit_environment()
        if sentry_sdk is None:
            logger.info(
                "Sentry disabled (sentry_sdk not installed, env=%s)",
                explicit_environment,
            )
            return

        if explicit_environment in _SENTRY_NON_REPORTING_ENVIRONMENTS:
            logger.info(
                "Sentry disabled (environment=%s does not send events)",
                explicit_environment,
            )
            return

        if not settings.SENTRY_DSN:
            logger.info(
                "Sentry disabled (SENTRY_DSN not set, env=%s)",
                explicit_environment,
            )
            return

        environment = _sentry_environment_label(explicit_environment)
        init_kwargs: dict[str, Any] = {
            "dsn": settings.SENTRY_DSN,
            "environment": environment,
            "release": _sentry_release(),
            "traces_sample_rate": settings.SENTRY_TRACES_SAMPLE_RATE,
            "profiles_sample_rate": settings.SENTRY_PROFILES_SAMPLE_RATE,
            "send_default_pii": settings.SENTRY_SEND_PII,
            "enable_logs": settings.SENTRY_ENABLE_LOGS,
            "enable_metrics": settings.SENTRY_ENABLE_METRICS,
            "before_send": _drop_expected_events,
            "integrations": [
                StarletteIntegration(
                    failed_request_status_codes={500, 501, 502, 504},
                ),
                FastApiIntegration(
                    failed_request_status_codes={500, 501, 502, 504},
                ),
                SqlalchemyIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
        }
        if settings.SENTRY_PROFILE_SESSION_SAMPLE_RATE is not None:
            init_kwargs["profile_session_sample_rate"] = (
                settings.SENTRY_PROFILE_SESSION_SAMPLE_RATE
            )
        if settings.SENTRY_PROFILE_LIFECYCLE is not None:
            init_kwargs["profile_lifecycle"] = settings.SENTRY_PROFILE_LIFECYCLE

        sentry_sdk.init(**init_kwargs)
        self._enabled = True
        logger.info("Sentry initialized (env=%s)", environment)

    def capture_exception(
        self,
        error: BaseException,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        if not self._can_use_sdk():
            return

        self._apply_safe_context(context)
        sentry_sdk.capture_exception(error)

    def capture_message(
        self,
        message: str,
        *,
        level: str = "info",
        context: dict[str, Any] | None = None,
    ) -> None:
        if not self._can_use_sdk():
            return

        self._apply_safe_context(context)
        sentry_sdk.capture_message(message, level=_normalize_message_level(level))

    def log_event(
        self,
        level: str,
        message: str,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if not self._can_use_sdk() or not hasattr(sentry_sdk, "logger"):
            return

        log_level = _normalize_log_level(level)
        log_method = getattr(sentry_sdk.logger, log_level)
        log_method(message, attributes=filter_safe_attributes(attributes))

    def increment_metric(
        self,
        name: str,
        value: float = 1.0,
        *,
        unit: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if not self._can_use_sdk():
            return

        sentry_sdk.metrics.count(
            name,
            value,
            unit=unit,
            attributes=filter_safe_attributes(attributes),
        )

    def gauge_metric(
        self,
        name: str,
        value: float,
        *,
        unit: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if not self._can_use_sdk():
            return

        sentry_sdk.metrics.gauge(
            name,
            value,
            unit=unit,
            attributes=filter_safe_attributes(attributes),
        )

    def distribution_metric(
        self,
        name: str,
        value: float,
        *,
        unit: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        if not self._can_use_sdk():
            return

        sentry_sdk.metrics.distribution(
            name,
            value,
            unit=unit,
            attributes=filter_safe_attributes(attributes),
        )

    def set_request_context(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        user_id: str | None = None,
    ) -> None:
        if not self._can_use_sdk():
            return

        context = {
            "request_id": request_id,
            "method": method,
            "path": path,
            "route": path,
            "environment": _sentry_environment_label(_explicit_environment()),
            "release": _sentry_release(),
            "user_id": user_id,
        }
        self._apply_safe_context(context)
        if user_id:
            sentry_sdk.set_user({"id": user_id})

    def start_span(
        self,
        *,
        operation: str,
        description: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> AbstractContextManager[Any]:
        if not self._can_use_sdk():
            return nullcontext()

        self._apply_safe_context(
            {"operation": operation, **filter_safe_context(context)}
        )
        return sentry_sdk.start_span(op=operation, description=description)

    def flush(self, *, timeout: float = 5) -> None:
        if not self._can_use_sdk():
            return
        sentry_sdk.flush(timeout=timeout)

    def _can_use_sdk(self) -> bool:
        return bool(self._enabled and sentry_sdk is not None)

    def _apply_safe_context(self, context: dict[str, Any] | None) -> None:
        safe_context = filter_safe_context(context)
        if not safe_context:
            return

        sentry_sdk.set_context("mealtrack", safe_context)
        for key, value in filter_safe_tags(safe_context).items():
            sentry_sdk.set_tag(key, value)


def initialize_sentry() -> None:
    """Backward-compatible Sentry initialization wrapper."""
    SentryObservabilityConnector().initialize()


def _drop_expected_events(
    event: dict[str, Any], hint: dict[str, Any]
) -> dict[str, Any] | None:
    """Drop expected signals that are not backend failures.

    Provider safety-filter rejections of user input are mapped to HTTP 422,
    but the LangChain integration captures the raw provider error before that
    mapping, so those events are dropped here.

    A direct ``asyncio.CancelledError`` on the chat message routes is the
    ASGI request task cancelling in-flight streaming after the client
    disconnects. Those routes are ``POST /v1/chat/messages`` and
    ``POST /v1/chat/threads/{thread_id}/messages``. Chained non-cancellation
    causes and contexts stay visible, as do other routes and events with no
    original exception.

    ``GeneratorExit`` captured by the LangChain integration is the provider
    stream closing after a safety stop. Only that origin is dropped.
    SQLAlchemy's "Exception terminating connection" log is dropped when the
    logging integration attached a bare ``CancelledError``.
    """
    exc_info = hint.get("exc_info")
    exc = exc_info[1] if exc_info else None
    if isinstance(exc, AIContentRejectedError):
        return None
    if getattr(exc, "code", None) in CONTENT_REJECTION_ERROR_CODES:
        return None
    if _is_langchain_generator_exit(event, exc_info):
        return None
    if _is_expected_chat_cancellation(event, exc_info):
        return None
    if _is_expected_connection_terminate(event, exc_info):
        return None
    return event


def _is_expected_chat_cancellation(event: dict[str, Any], exc_info: Any) -> bool:
    """True for a direct client disconnect on a chat messages route."""
    if event.get("transaction") not in _CHAT_MESSAGE_TRANSACTIONS:
        return False
    return _is_bare_cancellation(exc_info)


def _is_langchain_generator_exit(event: dict[str, Any], exc_info: Any) -> bool:
    """True when LangChain captured ``GeneratorExit`` from a closed stream.

    The origin ``auto.ai.langchain`` is the integration's span origin. The
    same string on the exception mechanism counts. Other ``GeneratorExit``
    events and other exceptions on that span stay visible.
    """
    if not _is_generator_exit(exc_info):
        return False
    return _has_langchain_origin(event)


def _is_generator_exit(exc_info: Any) -> bool:
    if not isinstance(exc_info, tuple) or len(exc_info) < 2:
        return False
    return isinstance(exc_info[1], GeneratorExit)


def _has_langchain_origin(event: dict[str, Any]) -> bool:
    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        trace = contexts.get("trace")
        if isinstance(trace, dict) and trace.get("origin") == _LANGCHAIN_ORIGIN:
            return True
    spans = event.get("spans")
    if isinstance(spans, list):
        for span in spans:
            if isinstance(span, dict) and span.get("origin") == _LANGCHAIN_ORIGIN:
                return True
    for value in _exception_values(event):
        mechanism = value.get("mechanism")
        if not isinstance(mechanism, dict):
            continue
        if mechanism.get("type") == _LANGCHAIN_ORIGIN:
            return True
        data = mechanism.get("data")
        if isinstance(data, dict) and data.get("origin") == _LANGCHAIN_ORIGIN:
            return True
    return False


def _is_expected_connection_terminate(event: dict[str, Any], exc_info: Any) -> bool:
    """True for SQLAlchemy's pool log of a cancel during connection close.

    The logging integration records mechanism type ``logging`` and the
    message "Exception terminating connection". A different exception on
    that log line stays visible.
    """
    if not _is_bare_cancellation(exc_info):
        return False
    if not _has_logging_mechanism(event):
        return False
    return _CONNECTION_TERMINATE_MESSAGE in _event_log_message(event)


def _has_logging_mechanism(event: dict[str, Any]) -> bool:
    for value in _exception_values(event):
        mechanism = value.get("mechanism")
        if isinstance(mechanism, dict) and mechanism.get("type") == "logging":
            return True
    return False


def _event_log_message(event: dict[str, Any]) -> str:
    parts: list[str] = []
    message = event.get("message")
    if isinstance(message, str):
        parts.append(message)
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for key in ("message", "formatted"):
            value = logentry.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def _exception_values(event: dict[str, Any]) -> list[dict[str, Any]]:
    exception = event.get("exception")
    if not isinstance(exception, dict):
        return []
    values = exception.get("values")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _is_bare_cancellation(exc_info: Any) -> bool:
    """True for ``CancelledError`` with no unrelated cause or context."""
    if not isinstance(exc_info, tuple) or len(exc_info) < 2:
        return False

    error = exc_info[1]
    if not isinstance(error, asyncio.CancelledError):
        return False

    for related_error in (error.__cause__, error.__context__):
        if related_error is not None and not isinstance(
            related_error, asyncio.CancelledError
        ):
            return False
    return True


def _env_value(name: str) -> str | None:
    """Return a stripped, lowercased process env var, or None when blank."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip().lower()


def _explicit_environment() -> str | None:
    """Return ENVIRONMENT when it is set to a non-blank value.

    ``None`` means unset or blank. That is not an opt-out: settings defaults
    the same name to ``development``, and treating the default as local would
    disable Sentry in any process that forgot the variable. SENTRY_ENVIRONMENT
    is not an opt-out either; it only names the environment on events we send.
    """
    return _env_value("ENVIRONMENT")


def _sentry_release() -> str | None:
    """Release passed to sentry_sdk.init and request context.

    An explicit ``SENTRY_RELEASE`` wins, then the image build SHA in
    ``GIT_SHA``. Otherwise ``settings.SENTRY_RELEASE``, which is ``None``
    when unset so the SDK keeps its own fallback (``git rev-parse HEAD``
    when a checkout is present, then ``SOURCE_VERSION`` and similar).
    Values are stripped and not lowercased. A blank variable is unset.
    """
    for name in ("SENTRY_RELEASE", "GIT_SHA"):
        raw = os.environ.get(name)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return settings.SENTRY_RELEASE


def _sentry_environment_label(explicit: str | None) -> str:
    """Label passed to sentry_sdk.init and request context.

    SENTRY_ENVIRONMENT wins, then ENVIRONMENT. Both are the process env vars.
    Passing the label ourselves matters because an explicit ``environment=``
    argument overrides the SDK's own SENTRY_ENVIRONMENT read. settings
    defaults ENVIRONMENT to ``development``, so a missing variable must not
    use that value. The neutral label is ``unknown``.
    """
    sentry_environment = _env_value("SENTRY_ENVIRONMENT")
    if sentry_environment is not None:
        return sentry_environment
    if explicit is not None:
        return explicit
    return "unknown"


def _normalize_message_level(level: str) -> SentryMessageLevel:
    if level in _SENTRY_MESSAGE_LEVELS:
        return cast(SentryMessageLevel, level)
    return "info"


def _normalize_log_level(level: str) -> SentryLogLevel:
    if level == "critical":
        return "fatal"
    if level in _SENTRY_LOG_LEVELS:
        return cast(SentryLogLevel, level)
    return "info"
