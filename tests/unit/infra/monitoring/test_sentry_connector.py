"""Tests for the Sentry observability connector."""

import asyncio
from contextlib import nullcontext
from unittest.mock import MagicMock

import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from src.domain.exceptions.ai_exceptions import AIContentRejectedError
from src.domain.exceptions.weekly_meal_planner_exceptions import (
    WeeklyMealPlanConflictError,
    WeeklyMealPlanIdempotencyConflictError,
)
from src.infra.monitoring.connectors import (
    filter_safe_attributes,
    filter_safe_context,
    filter_safe_tags,
)
from src.infra.monitoring.sentry import (
    SentryObservabilityConnector,
    _drop_expected_events,
    add_connect_retry_breadcrumb,
)


class _ProviderError(Exception):
    def __init__(self, code):
        super().__init__("provider error")
        self.code = code


def _hint(exc):
    return {"exc_info": (type(exc), exc, None)}


def test_before_send_redacts_api_key_query_values():
    secret = "synthetic-fdc-key-not-real"
    leaked = (
        "Client error '404 Not Found' for url "
        f"'https://api.nal.usda.gov/fdc/v1/food/12345?api_key={secret}'"
    )
    event = {
        "level": "error",
        "message": f"Error handling GetFoodDetailsQuery: {leaked}",
        "logentry": {"message": leaked, "formatted": leaked},
        "exception": {"values": [{"type": "HTTPStatusError", "value": leaked}]},
        "request": {"url": f"https://api.example.test/food?api_key={secret}"},
    }

    result = _drop_expected_events(event, {"exc_info": None})

    assert result is event
    rendered = str(result)
    assert secret not in rendered
    assert "api_key=[Filtered]" in result["exception"]["values"][0]["value"]
    assert "api_key=[Filtered]" in result["request"]["url"]


def test_before_send_drops_content_rejections():
    event = {"level": "error"}
    chat = {"level": "error", "transaction": "/v1/chat/messages"}

    assert _drop_expected_events(event, _hint(AIContentRejectedError("x"))) is None
    assert _drop_expected_events(chat, _hint(AIContentRejectedError("blocked"))) is None
    assert _drop_expected_events(event, _hint(_ProviderError("invalid_prompt"))) is None
    assert _drop_expected_events(event, _hint(_ProviderError("content_filter"))) is None
    assert (
        _drop_expected_events(event, _hint(_ProviderError("content_policy_violation")))
        is None
    )


def test_before_send_drops_expected_chat_cancellations():
    """Fails while chat cancellations still pass through before_send.

    Production signature from PYTHON-HY / PYTHON-HW / PYTHON-HR / PYTHON-HP:
    CancelledError raised by the ASGI request task on POST /v1/chat/messages.
    """
    event = {
        "level": "error",
        "transaction": "/v1/chat/messages",
        "exception": {
            "values": [
                {
                    "type": "CancelledError",
                    "value": (
                        "Cancelled via cancel scope 76eea13091d0 by "
                        "<Task pending name='Task-725790' "
                        "coro=<RequestResponseCycle.run_asgi() "
                        "running at uvicorn/protocols/http/httptools_impl.py:421>"
                    ),
                }
            ]
        },
    }
    scoped = asyncio.CancelledError(
        "Cancelled via cancel scope 76eea13091d0 by "
        "<Task pending name='Task-725790' "
        "coro=<RequestResponseCycle.run_asgi() "
        "running at uvicorn/protocols/http/httptools_impl.py:421>"
    )
    empty = asyncio.CancelledError()
    nested = asyncio.CancelledError("cancelled")
    nested.__context__ = asyncio.CancelledError()

    assert _drop_expected_events(event, _hint(scoped)) is None
    assert _drop_expected_events(event, _hint(empty)) is None
    assert _drop_expected_events(event, _hint(nested)) is None


def test_before_send_drops_langchain_generator_exit():
    """PYTHON-HF: aclose() throws GeneratorExit into the LangChain span.

    The integration's span origin is ``auto.ai.langchain``. The exception
    mechanism stays generic. Either the span origin or the same origin on
    the mechanism identifies that capture. A safety stop is not a failure.
    """
    exit_error = GeneratorExit()
    span_event = {
        "level": "error",
        "transaction": "/v1/chat/threads/{thread_id}/messages",
        "exception": {
            "values": [
                {
                    "type": "GeneratorExit",
                    "value": "",
                    "mechanism": {"type": "generic", "handled": True},
                }
            ]
        },
        "contexts": {
            "trace": {
                "op": "gen_ai.chat",
                "origin": "auto.ai.langchain",
            }
        },
    }
    mechanism_event = {
        "level": "error",
        "exception": {
            "values": [
                {
                    "type": "GeneratorExit",
                    "value": "",
                    "mechanism": {
                        "type": "generic",
                        "handled": True,
                        "data": {"origin": "auto.ai.langchain"},
                    },
                }
            ]
        },
    }

    assert _drop_expected_events(span_event, _hint(exit_error)) is None
    assert _drop_expected_events(mechanism_event, _hint(GeneratorExit())) is None


def test_before_send_keeps_generator_exit_without_langchain_origin():
    event = {
        "level": "error",
        "exception": {
            "values": [
                {
                    "type": "GeneratorExit",
                    "mechanism": {"type": "generic", "handled": True},
                }
            ]
        },
        "contexts": {"trace": {"op": "http.server", "origin": "auto.http.starlette"}},
    }

    assert _drop_expected_events(event, _hint(GeneratorExit())) is event


def test_before_send_keeps_langchain_errors_that_are_not_generator_exit():
    event = {
        "level": "error",
        "transaction": "/v1/chat/threads/{thread_id}/messages",
        "contexts": {"trace": {"op": "gen_ai.chat", "origin": "auto.ai.langchain"}},
        "exception": {
            "values": [
                {
                    "type": "RuntimeError",
                    "mechanism": {"type": "generic", "handled": True},
                }
            ]
        },
    }

    assert _drop_expected_events(event, _hint(RuntimeError("boom"))) is event


def test_before_send_drops_cancelled_error_on_both_chat_message_routes():
    """PYTHON-HW / PYTHON-HP use the thread route. PYTHON-HY uses /messages."""
    plain = asyncio.CancelledError("Cancelled via cancel scope")
    for transaction in (
        "/v1/chat/messages",
        "/v1/chat/threads/{thread_id}/messages",
    ):
        event = {"level": "error", "transaction": transaction}
        assert _drop_expected_events(event, _hint(plain)) is None

    other_route = {
        "level": "error",
        "transaction": "/v1/meals/scan-by-url",
    }
    assert _drop_expected_events(other_route, _hint(plain)) is other_route


# PYTHON-HY 4a3e4fffd79b40988114d36f392fe5dc and PYTHON-HW
# 902c8dd97b364019a75445ef473e3f39. Task ids only; no user or model text.
_HY_CANCEL_MESSAGE = (
    "Cancelled via cancel scope 799b40ad3f70 by "
    "<Task pending name='Task-196336' "
    "coro=<RequestResponseCycle.run_asgi() "
    "running at uvicorn/protocols/http/httptools_impl.py:421> "
    "cb=[set.discard()]>"
)
_HW_CANCEL_MESSAGE = (
    "Cancelled via cancel scope 799b40143750 by "
    "<Task pending name='Task-246698' "
    "coro=<RequestResponseCycle.run_asgi() "
    "running at uvicorn/protocols/http/httptools_impl.py:421> "
    "cb=[set.discard()]>"
)
_HY_URL = "http://api.nutreeai.com/v1/chat/messages"
_HW_URL = (
    "http://api.nutreeai.com/v1/chat/threads/"
    "00000000-0000-4000-8000-000000000001/messages"
)


def _disconnect_cancelled(message: str) -> asyncio.CancelledError:
    """httpcore ``raise exc from None`` (pool.py 256 for HY, 407 for HW).

    ``from None`` sets ``__suppress_context__`` and leaves ``__context__``.
    Sentry stores only the CancelledError. A bare check of ``__context__``
    still sees the cleanup error.
    """
    try:
        raise RuntimeError("connection cleanup")
    except RuntimeError:
        try:
            raise asyncio.CancelledError(message)
        except asyncio.CancelledError as exc:
            try:
                raise exc from None
            except asyncio.CancelledError as hidden:
                return hidden


def _stored_disconnect_event(*, transaction: str, url: str, message: str) -> dict:
    return {
        "level": "error",
        "transaction": transaction,
        "request": {"method": "POST", "url": url},
        "exception": {
            "values": [
                {
                    "type": "CancelledError",
                    "value": message,
                    "mechanism": {"type": "generic", "handled": True},
                }
            ]
        },
    }


def test_before_send_drops_hy_and_hw_when_context_is_suppressed():
    """The live object is not a bare CancelledError. Sentry still shows one."""
    cases = (
        (
            "/v1/chat/messages",
            _HY_URL,
            _HY_CANCEL_MESSAGE,
        ),
        (
            "/v1/chat/threads/{thread_id}/messages",
            _HW_URL,
            _HW_CANCEL_MESSAGE,
        ),
    )
    for transaction, url, message in cases:
        cancelled = _disconnect_cancelled(message)
        assert cancelled.__cause__ is None
        assert cancelled.__suppress_context__ is True
        assert isinstance(cancelled.__context__, RuntimeError)
        event = _stored_disconnect_event(
            transaction=transaction, url=url, message=message
        )
        assert _drop_expected_events(event, _hint(cancelled)) is None

    other = _stored_disconnect_event(
        transaction="/v1/meals/scan-by-url",
        url="http://api.nutreeai.com/v1/meals/scan-by-url",
        message=_HY_CANCEL_MESSAGE,
    )
    assert (
        _drop_expected_events(other, _hint(_disconnect_cancelled(_HY_CANCEL_MESSAGE)))
        is other
    )

    caused = _disconnect_cancelled(_HY_CANCEL_MESSAGE)
    caused.__suppress_context__ = False
    caused.__cause__ = RuntimeError("provider failed")
    chat = _stored_disconnect_event(
        transaction="/v1/chat/messages",
        url=_HY_URL,
        message=_HY_CANCEL_MESSAGE,
    )
    assert _drop_expected_events(chat, _hint(caused)) is chat


def test_before_send_drops_stored_disconnect_when_hint_has_no_exc_info():
    """LangChain capture can reach before_send with the payload and no hint."""
    for transaction, url, message in (
        ("/v1/chat/messages", _HY_URL, _HY_CANCEL_MESSAGE),
        (
            "/v1/chat/threads/{thread_id}/messages",
            _HW_URL,
            _HW_CANCEL_MESSAGE,
        ),
    ):
        event = _stored_disconnect_event(
            transaction=transaction, url=url, message=message
        )
        assert _drop_expected_events(event, {}) is None

    url_only = {
        "level": "error",
        "request": {"method": "POST", "url": _HW_URL},
        "exception": {
            "values": [
                {
                    "type": "CancelledError",
                    "value": _HW_CANCEL_MESSAGE,
                    "mechanism": {"type": "generic", "handled": True},
                }
            ]
        },
    }
    assert _drop_expected_events(url_only, {}) is None

    chained = _stored_disconnect_event(
        transaction="/v1/chat/messages",
        url=_HY_URL,
        message=_HY_CANCEL_MESSAGE,
    )
    chained["exception"]["values"].append(
        {
            "type": "RuntimeError",
            "value": "provider failed",
            "mechanism": {"type": "chained", "source": "__cause__"},
        }
    )
    assert _drop_expected_events(chained, {}) is chained


class _CapturingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)


def _error_events(envelopes: list[Envelope]) -> list[dict]:
    events = []
    for envelope in envelopes:
        event = envelope.get_event()
        if event is not None and event.get("type") != "transaction":
            events.append(event)
    return events


def test_sdk_before_send_drops_hy_and_hw_payloads():
    """Push the production captures through a real client, as before_send runs."""
    transport = _CapturingTransport()
    sentry_sdk.init(
        dsn="https://public@example.ingest.sentry.io/1",
        transport=transport,
        before_send=_drop_expected_events,
        default_integrations=False,
        traces_sample_rate=0,
    )
    try:
        captures = (
            (
                "/v1/chat/messages",
                _HY_URL,
                _HY_CANCEL_MESSAGE,
            ),
            (
                "/v1/chat/threads/{thread_id}/messages",
                _HW_URL,
                _HW_CANCEL_MESSAGE,
            ),
        )
        for transaction, url, message in captures:
            with sentry_sdk.isolation_scope() as scope:
                scope.set_transaction_name(transaction)

                def _add_request(event, hint, request_url=url):
                    event["request"] = {"method": "POST", "url": request_url}
                    return event

                scope.add_event_processor(_add_request)
                sentry_sdk.capture_exception(_disconnect_cancelled(message))

        with sentry_sdk.isolation_scope() as scope:
            scope.set_transaction_name("/v1/chat/messages")
            visible = _disconnect_cancelled(_HY_CANCEL_MESSAGE)
            visible.__suppress_context__ = False
            visible.__cause__ = RuntimeError("provider failed")
            sentry_sdk.capture_exception(visible)
    finally:
        sentry_sdk.get_client().close()

    errors = _error_events(transport.envelopes)
    assert len(errors) == 1
    assert errors[0]["transaction"] == "/v1/chat/messages"
    assert {value["type"] for value in errors[0]["exception"]["values"]} == {
        "CancelledError",
        "RuntimeError",
    }


def _connection_terminate_event(*, exc_type: str) -> dict:
    return {
        "level": "error",
        "logger": "src.infra.database.planner_pool.PlannerQueuePool",
        "logentry": {
            "message": "Exception terminating connection %r",
            "formatted": (
                "Exception terminating connection "
                "<AdaptedConnection <asyncpg.connection.Connection object>>"
            ),
        },
        "exception": {
            "values": [
                {
                    "type": exc_type,
                    "mechanism": {"type": "logging", "handled": True},
                }
            ]
        },
    }


def test_before_send_drops_logging_connection_terminate_cancelled_error():
    """PYTHON-HP f3faf05d: pool logger, logging mechanism, bare CancelledError."""
    event = _connection_terminate_event(exc_type="CancelledError")
    cancelled = asyncio.CancelledError(
        "Cancelled via cancel scope 799b40143750 by "
        "<Task pending name='Task-246698' "
        "coro=<RequestResponseCycle.run_asgi() "
        "running at uvicorn/protocols/http/httptools_impl.py:421>"
    )

    assert _drop_expected_events(event, _hint(cancelled)) is None


def test_before_send_keeps_connection_terminate_that_is_not_cancelled():
    event = _connection_terminate_event(exc_type="RuntimeError")

    assert _drop_expected_events(event, _hint(RuntimeError("close failed"))) is event


def test_before_send_drops_python_hp_pool_terminate_on_a_langchain_span():
    """PYTHON-HP ceeb4be9: planner pool log, CancelledError, LangChain span.

    The logging integration can arrive with the message as an object and
    with no exc_info. A real error on the same log line is kept.
    """
    event = _connection_terminate_event(exc_type="CancelledError")
    event["transaction"] = "/v1/chat/threads/{thread_id}/messages"
    event["message"] = {
        "formatted": (
            "Exception terminating connection "
            "<AdaptedConnection <asyncpg.connection.Connection object>>"
        )
    }
    event["contexts"] = {"trace": {"op": "gen_ai.chat", "origin": "auto.ai.langchain"}}
    event["request"] = {
        "method": "POST",
        "url": (
            "http://api.nutreeai.com/v1/chat/threads/"
            "8115178b-7098-44a1-aadb-2be8e752efad/messages"
        ),
    }
    cancelled = asyncio.CancelledError(
        "Cancelled via cancel scope 7e91fd4aea30 by "
        "<Task pending name='Task-1432391' "
        "coro=<RequestResponseCycle.run_asgi() "
        "running at uvicorn/protocols/http/httptools_impl.py:421>"
    )

    assert _drop_expected_events(event, _hint(cancelled)) is None
    assert _drop_expected_events(event, {}) is None

    kept = _connection_terminate_event(exc_type="RuntimeError")
    kept["contexts"] = {"trace": {"op": "gen_ai.chat", "origin": "auto.ai.langchain"}}
    assert _drop_expected_events(kept, _hint(RuntimeError("close failed"))) is kept


def test_before_send_keeps_langchain_cancellation_off_chat_routes():
    event = {
        "level": "error",
        "transaction": "/v1/meals/scan-by-url",
        "contexts": {"trace": {"op": "gen_ai.chat", "origin": "auto.ai.langchain"}},
        "exception": {
            "values": [{"type": "CancelledError", "mechanism": {"type": "generic"}}]
        },
    }

    assert _drop_expected_events(event, _hint(asyncio.CancelledError())) is event


def test_before_send_keeps_chat_cancellations_that_are_not_client_disconnects():
    event = {"level": "error", "transaction": "/v1/chat/messages"}
    provider_error = RuntimeError("provider failed")
    caused = asyncio.CancelledError("cancelled")
    caused.__cause__ = provider_error
    contextual = asyncio.CancelledError("cancelled")
    contextual.__context__ = provider_error
    other_route = {**event, "transaction": "/v1/meals/scan-by-url"}
    plain = asyncio.CancelledError("Cancelled via cancel scope")

    assert _drop_expected_events(event, _hint(provider_error)) is event
    assert _drop_expected_events(event, _hint(caused)) is event
    assert _drop_expected_events(event, _hint(contextual)) is event
    assert _drop_expected_events(event, {}) is event
    assert _drop_expected_events(other_route, _hint(plain)) is other_route


def test_before_send_keeps_weekly_meal_plan_conflicts():
    """A type filter would hide unexpected bugs that use this exception."""
    event = {"level": "error"}
    conflict = WeeklyMealPlanConflictError(
        "Logged weekly meal slots cannot be replaced"
    )
    idempotency = WeeklyMealPlanIdempotencyConflictError()

    assert _drop_expected_events(event, _hint(conflict)) is event
    assert _drop_expected_events(event, _hint(idempotency)) is event


def test_before_send_keeps_other_events():
    event = {"level": "error"}

    assert _drop_expected_events(event, _hint(_ProviderError("rate_limit"))) is event
    assert _drop_expected_events(event, _hint(RuntimeError("boom"))) is event
    assert _drop_expected_events(event, {}) is event


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


def test_initialize_does_not_send_for_development_or_local(monkeypatch):
    """Local Macs with a DSN and ENVIRONMENT=development were publishing events."""
    for environment in ("development", "local", "Development", "LOCAL", " local "):
        mock_sdk = MagicMock()
        monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
        monkeypatch.setenv("ENVIRONMENT", environment)
        monkeypatch.setenv("SENTRY_ENVIRONMENT", "production")
        monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
        monkeypatch.setattr(
            "src.infra.monitoring.sentry.settings.ENVIRONMENT", "production"
        )

        connector = SentryObservabilityConnector()
        connector.initialize()

        mock_sdk.init.assert_not_called()
        assert connector._enabled is False


def test_initialize_sends_when_environment_is_unset_or_blank(monkeypatch):
    """A missing ENVIRONMENT must not silence a process that has a DSN."""
    for raw in (None, "", "   "):
        mock_sdk = MagicMock()
        monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
        if raw is None:
            monkeypatch.delenv("ENVIRONMENT", raising=False)
        else:
            monkeypatch.setenv("ENVIRONMENT", raw)
        monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
        monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
        monkeypatch.setattr(
            "src.infra.monitoring.sentry.settings.ENVIRONMENT", "development"
        )

        connector = SentryObservabilityConnector()
        connector.initialize()

        mock_sdk.init.assert_called_once()
        init_kwargs = mock_sdk.init.call_args.kwargs
        assert init_kwargs["environment"] == "unknown"
        assert "ignore_errors" not in init_kwargs
        assert connector._enabled is True


def test_initialize_environment_comes_from_environment_env_var(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "   ")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.ENVIRONMENT", "development"
    )
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.SENTRY_TRACES_SAMPLE_RATE", 0.1
    )
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.SENTRY_PROFILES_SAMPLE_RATE", 0.05
    )

    connector = SentryObservabilityConnector()
    connector.initialize()

    init_kwargs = mock_sdk.init.call_args.kwargs
    assert init_kwargs["environment"] == "production"
    assert init_kwargs["traces_sample_rate"] == 0.1
    assert init_kwargs["profiles_sample_rate"] == 0.05
    assert "ignore_errors" not in init_kwargs
    assert connector._enabled is True


def test_initialize_prefers_sentry_environment_over_environment(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "Staging")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.ENVIRONMENT", "development"
    )

    connector = SentryObservabilityConnector()
    connector.initialize()

    init_kwargs = mock_sdk.init.call_args.kwargs
    assert init_kwargs["environment"] == "staging"
    assert connector._enabled is True


def test_initialize_sentry_environment_does_not_opt_out(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "development")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.ENVIRONMENT", "development"
    )

    connector = SentryObservabilityConnector()
    connector.initialize()

    init_kwargs = mock_sdk.init.call_args.kwargs
    assert init_kwargs["environment"] == "development"
    assert connector._enabled is True


def test_initialize_skips_when_dsn_unset(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", None)

    connector = SentryObservabilityConnector()
    connector.initialize()

    mock_sdk.init.assert_not_called()


def test_initialize_release_uses_git_sha_when_sentry_release_unset(monkeypatch):
    """Image builds export GIT_SHA. Init must use it when SENTRY_RELEASE is unset.

    Fails while init still passes settings.SENTRY_RELEASE only: production
    images have no git binary and no .git, so the SDK fallback is empty and
    the release stays None.
    """
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("GIT_SHA", "abc123def456")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_RELEASE", None)

    connector = SentryObservabilityConnector()
    connector.initialize()

    init_kwargs = mock_sdk.init.call_args.kwargs
    assert init_kwargs["release"] == "abc123def456"
    assert init_kwargs["environment"] == "production"
    assert init_kwargs["before_send"] is _drop_expected_events

    connector.set_request_context(
        request_id="abc12345",
        method="POST",
        path="/v1/chat/messages",
    )
    assert mock_sdk.set_context.call_args.args[1]["release"] == "abc123def456"


def test_initialize_release_prefers_explicit_sentry_release_over_git_sha(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SENTRY_RELEASE", " explicit-rel ")
    monkeypatch.setenv("GIT_SHA", "abc123def456")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_DSN", "dsn")
    monkeypatch.setattr("src.infra.monitoring.sentry.settings.SENTRY_RELEASE", None)

    connector = SentryObservabilityConnector()
    connector.initialize()

    assert mock_sdk.init.call_args.kwargs["release"] == "explicit-rel"
    assert mock_sdk.init.call_args.kwargs["environment"] == "production"


def test_initialize_configures_integrations_and_release(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "test")
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
    assert init_kwargs["before_send"] is _drop_expected_events
    assert len(init_kwargs["integrations"]) == 4
    starlette, fastapi, *_ = init_kwargs["integrations"]
    assert starlette.failed_request_status_codes == {500, 501, 502, 504}
    assert fastapi.failed_request_status_codes == {500, 501, 502, 504}


def test_initialize_omits_optional_profile_session_settings_when_unset(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "staging")
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
    monkeypatch.delenv("SENTRY_RELEASE", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.ENVIRONMENT", "development"
    )
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
    assert safe_context["environment"] == "staging"
    assert safe_context["release"] == "rel-1"
    mock_sdk.set_user.assert_called_once_with({"id": "user-1"})


def test_request_context_uses_unknown_when_env_vars_are_unset(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(
        "src.infra.monitoring.sentry.settings.ENVIRONMENT", "development"
    )

    connector = SentryObservabilityConnector()
    connector._enabled = True
    connector.set_request_context(
        request_id="abc12345",
        method="GET",
        path="/v1/meals",
    )

    safe_context = mock_sdk.set_context.call_args.args[1]
    assert safe_context["environment"] == "unknown"


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


def test_connect_retry_breadcrumb_has_no_user_data(monkeypatch):
    mock_sdk = MagicMock()
    monkeypatch.setattr("src.infra.monitoring.sentry.sentry_sdk", mock_sdk)

    add_connect_retry_breadcrumb("TimeoutError")

    mock_sdk.add_breadcrumb.assert_called_once_with(
        category="db.connect",
        level="warning",
        message="Database connect attempt failed; retrying once",
        data={"error_type": "TimeoutError"},
    )
