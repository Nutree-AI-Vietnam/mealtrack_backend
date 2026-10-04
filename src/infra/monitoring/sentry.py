"""Sentry observability connector for the MealTrack API."""

from __future__ import annotations

import asyncio
import logging
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

from src.infra.config.settings import settings
from src.infra.monitoring.connectors import (
    filter_safe_attributes,
    filter_safe_context,
    filter_safe_tags,
)

logger = logging.getLogger(__name__)

SentryMessageLevel = Literal["fatal", "critical", "error", "warning", "info", "debug"]
SentryLogLevel = Literal["fatal", "error", "warning", "info", "debug", "trace"]
_SENTRY_MESSAGE_LEVELS = {"fatal", "critical", "error", "warning", "info", "debug"}
_SENTRY_LOG_LEVELS = {"fatal", "error", "warning", "info", "debug", "trace"}


def _drop_expected_chat_cancellation(
    event: dict[str, Any], hint: dict[str, Any]
) -> dict[str, Any] | None:
    """Drop client-disconnect cancellations from the chat error stream only."""
    if event.get("transaction") != "/v1/chat/messages":
        return event

    exc_info = hint.get("exc_info")
    if not isinstance(exc_info, tuple) or len(exc_info) < 2:
        return event

    error = exc_info[1]
    if not isinstance(error, asyncio.CancelledError):
        return event

    for related_error in (error.__cause__, error.__context__):
        if related_error is not None and not isinstance(
            related_error, asyncio.CancelledError
        ):
            return event
    return None


class SentryObservabilityConnector:
    """Sentry-backed observability connector.

    The rest of the codebase talks to ``observability.py`` so provider SDK
    details stay isolated here.
    """

    def __init__(self) -> None:
        self._enabled = False

    def initialize(self) -> None:
        """Initialize Sentry SDK if SENTRY_DSN is configured.

        Must be called BEFORE the FastAPI app is instantiated so integrations
        can patch Starlette/FastAPI middleware.
        """
        if sentry_sdk is None:
            logger.info("Sentry disabled (sentry_sdk not installed)")
            return

        if not settings.SENTRY_DSN:
            logger.info("Sentry disabled (SENTRY_DSN not set)")
            return

        init_kwargs: dict[str, Any] = {
            "dsn": settings.SENTRY_DSN,
            "environment": settings.ENVIRONMENT,
            "release": settings.SENTRY_RELEASE,
            "traces_sample_rate": settings.SENTRY_TRACES_SAMPLE_RATE,
            "profiles_sample_rate": settings.SENTRY_PROFILES_SAMPLE_RATE,
            "send_default_pii": settings.SENTRY_SEND_PII,
            "before_send": _drop_expected_chat_cancellation,
            "enable_logs": settings.SENTRY_ENABLE_LOGS,
            "enable_metrics": settings.SENTRY_ENABLE_METRICS,
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
        logger.info("Sentry initialized (env=%s)", settings.ENVIRONMENT)

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
            "environment": settings.ENVIRONMENT,
            "release": settings.SENTRY_RELEASE,
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
