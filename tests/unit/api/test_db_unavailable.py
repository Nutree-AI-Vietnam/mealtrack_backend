"""Database connect failures are 503 DB_UNAVAILABLE, not AI_UNAVAILABLE.

A builtin TimeoutError from the AI deadline and a builtin TimeoutError from
asyncpg's connect handshake are the same class. The connect path has to be
recognized by exception type or cause chain.
"""

import asyncio

import asyncpg
import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sqlalchemy.exc import OperationalError, StatementError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from src.api.exceptions import handle_exception
from src.domain.exceptions.database_exceptions import DatabaseUnavailableError
from src.infra.database.config_async import connect_with_one_retry
from src.infra.monitoring.sentry import (
    SentryObservabilityConnector,
    _drop_expected_events,
)
from src.observability import (
    reset_observability_connector_for_test,
    set_observability_connector,
)

_DB_MESSAGE = "The database is temporarily unavailable. Please try again."


def _assert_db_unavailable(exc: BaseException) -> None:
    http = handle_exception(exc)
    assert http.status_code == 503
    assert http.detail["error_code"] == "DB_UNAVAILABLE"
    assert http.detail["message"] == _DB_MESSAGE
    assert http.detail["details"] == {}
    assert "handshake stalled" not in str(http.detail)
    assert "QueuePool" not in str(http.detail)
    assert "AI meal generation" not in str(http.detail)


@pytest.mark.asyncio
async def test_db_connect_timeout_returns_503_db_unavailable():
    """asyncpg/SQLAlchemy connect TimeoutError, after the one retry, is DB."""

    async def connect(*args, **kwargs):
        raise TimeoutError("handshake stalled")

    with pytest.raises(Exception) as caught:
        await connect_with_one_retry(connect)

    _assert_db_unavailable(caught.value)


def test_pool_timeout_returns_503_db_unavailable():
    """sqlalchemy.exc.TimeoutError is the pool checkout timeout, not an AI one."""
    _assert_db_unavailable(
        SATimeoutError(
            "QueuePool limit of size 5 overflow 10 reached, "
            "connection timed out, timeout 30.00",
            code="3o7r",
        )
    )


def test_operational_error_caused_by_connect_timeout_returns_503_db_unavailable():
    """OperationalError whose orig is a connect TimeoutError is DB, not 500."""
    driver = TimeoutError("handshake stalled")
    _assert_db_unavailable(OperationalError("connect", {}, driver))


def test_ai_timeout_stays_ai_unavailable():
    http = handle_exception(TimeoutError("Planner provider deadline expired"))

    assert http.status_code == 503
    assert http.detail["error_code"] == "AI_UNAVAILABLE"
    assert http.detail["message"] == "AI meal generation is temporarily unavailable"
    assert "Planner provider deadline expired" not in str(http.detail)


@pytest.mark.asyncio
async def test_ai_wait_for_timeout_stays_ai_unavailable():
    with pytest.raises(TimeoutError) as caught:
        await asyncio.wait_for(asyncio.sleep(1), timeout=0.001)

    http = handle_exception(caught.value)
    assert http.status_code == 503
    assert http.detail["error_code"] == "AI_UNAVAILABLE"


def test_operational_error_without_a_timeout_stays_internal():
    http = handle_exception(OperationalError("SELECT", {}, RuntimeError("deadlock")))

    assert http.status_code == 500
    assert http.detail["error_code"] == "INTERNAL_ERROR"
    assert "deadlock" not in str(http.detail)


def test_statement_timeout_is_not_relabelled_as_database_unavailable():
    """A statement TimeoutError wrapped as StatementError is not a connect failure."""
    http = handle_exception(
        StatementError("query timed out", "SELECT 1", {}, TimeoutError("query"))
    )

    assert http.status_code == 500
    assert http.detail["error_code"] == "INTERNAL_ERROR"


def test_query_canceled_operational_error_stays_internal():
    """A mid-query cancel is not a connect timeout, so the client must not retry."""
    driver = asyncpg.exceptions.QueryCanceledError(
        "canceling statement due to user request"
    )
    http = handle_exception(OperationalError("SELECT 1", {}, driver))

    assert http.status_code == 500
    assert http.detail["error_code"] == "INTERNAL_ERROR"
    assert "canceling statement" not in str(http.detail)


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


def _prime_sentry_user(user: dict[str, str]) -> None:
    sentry_sdk.set_user(user)


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(DatabaseUnavailableError(), id="connect"),
        pytest.param(
            SATimeoutError(
                "QueuePool limit of size 5 overflow 10 reached, "
                "connection timed out, timeout 30.00",
                code="3o7r",
            ),
            id="pool",
        ),
        pytest.param(
            OperationalError("connect", {}, TimeoutError("handshake stalled")),
            id="operational",
        ),
    ],
)
def test_db_unavailable_sends_one_sentry_event(exc):
    """503 is outside FastAPI's Sentry filter, so the handler sends the event."""
    transport = _CapturingTransport()
    sentry_sdk.init(
        dsn="https://public@example.ingest.sentry.io/1",
        transport=transport,
        before_send=_drop_expected_events,
        default_integrations=False,
        send_default_pii=False,
    )
    connector = SentryObservabilityConnector()
    set_observability_connector(connector)
    _prime_sentry_user({"id": "user-1", "email": "person@example.com"})
    try:
        http = handle_exception(exc)
        sentry_sdk.flush(timeout=2)
    finally:
        sentry_sdk.get_client().close()
        reset_observability_connector_for_test()

    assert http.status_code == 503
    assert http.detail["error_code"] == "DB_UNAVAILABLE"
    events = _error_events(transport.envelopes)
    assert len(events) == 1
    event = events[0]
    assert event["fingerprint"] == ["db-unavailable", type(exc).__name__]
    assert event["tags"]["error_code"] == "DB_UNAVAILABLE"
    assert event.get("user") in (None, {})
    assert "request" not in event
    assert "person@example.com" not in _frame_vars(event)


def _frame_vars(event: dict) -> str:
    parts: list[str] = []
    exception = event.get("exception")
    if not isinstance(exception, dict):
        return ""
    for value in exception.get("values") or []:
        if not isinstance(value, dict):
            continue
        stack = value.get("stacktrace")
        if not isinstance(stack, dict):
            continue
        for frame in stack.get("frames") or []:
            if isinstance(frame, dict) and frame.get("vars") is not None:
                parts.append(str(frame["vars"]))
    return "\n".join(parts)


def test_operational_error_context_timeout_is_not_a_connect_timeout():
    """Only the explicit cause/orig chain counts, not a coincidental context."""
    try:
        raise TimeoutError("planner deadline")
    except TimeoutError:
        wrapped = OperationalError("SELECT", {}, RuntimeError("unrelated"))

    http = handle_exception(wrapped)
    assert http.status_code == 500
    assert http.detail["error_code"] == "INTERNAL_ERROR"
