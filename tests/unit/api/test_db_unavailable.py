"""Database connect failures are 503 DB_UNAVAILABLE, not AI_UNAVAILABLE.

A builtin TimeoutError from the AI deadline and a builtin TimeoutError from
asyncpg's connect handshake are the same class. The connect path has to be
recognized by exception type or cause chain.
"""

import asyncio

import pytest
from sqlalchemy.exc import OperationalError, StatementError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from src.api.exceptions import handle_exception
from src.infra.database.config_async import connect_with_one_retry

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


def test_operational_error_context_timeout_is_not_a_connect_timeout():
    """Only the explicit cause/orig chain counts, not a coincidental context."""
    try:
        raise TimeoutError("planner deadline")
    except TimeoutError:
        wrapped = OperationalError("SELECT", {}, RuntimeError("unrelated"))

    http = handle_exception(wrapped)
    assert http.status_code == 500
    assert http.detail["error_code"] == "INTERNAL_ERROR"
