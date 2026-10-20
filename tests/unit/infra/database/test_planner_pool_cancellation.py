"""A cancelled checkout must return its pooled connection (PYTHON-JZ).

Starlette cancels a ``StreamingResponse`` through an anyio cancel scope when
the client disconnects. An anyio cancel scope keeps re-cancelling every await
inside that scope. If the cancel lands while the pool pre-pings a connection
at checkout, SQLAlchemy invalidates the connection, and the asyncpg
``terminate()`` it runs is cancelled again. On SQLAlchemy 2.0.50 that second
``CancelledError`` escaped before the connection record was checked in, so
the slot stayed checked out until the garbage collector found it
("non-checked-in connection"). 2.0.55 checks the record back in from a
``finally``.

That re-cancel is specific to anyio cancel scopes. ``asyncio.timeout`` and
``Task.cancel()`` deliver one ``CancelledError``. A connection due for recycle
(``pool_recycle=120``) terminates on ``get_connection`` before the ping, and
that ``CancelledError`` is the caller's own timeout or cancel. It has to
propagate.

``PYTHON-HW`` is the ERROR log from ``terminate()`` ("Exception terminating
connection"). That log still happens; these tests do not treat it as fixed.

No database is needed: the fake below stands in for ``asyncpg.Connection``
behind SQLAlchemy's real asyncpg dialect, ``PlannerQueuePool`` and
``AsyncUnitOfWork``.
"""

from __future__ import annotations

import asyncio
import gc
import time
import warnings

import anyio
import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.responses import StreamingResponse

import src.infra.database.uow_async as uow_async
from src.infra.database.planner_pool import PlannerQueuePool


class _Attribute:
    name = "value"
    type = type("_Type", (), {"name": "text", "oid": 25})()


class _Statement:
    def __init__(self, sql: str) -> None:
        self._sql = sql.lower()

    def get_attributes(self):
        return [_Attribute()]

    def get_parameters(self):
        return []

    def get_statusmsg(self) -> str:
        return "SELECT 1"

    async def fetch(self, *args):
        if "version" in self._sql:
            return [("PostgreSQL 16.0",)]
        if "current_schema" in self._sql:
            return [("public",)]
        if "standard_conforming" in self._sql:
            return [("on",)]
        return [("1",)]


class _Transaction:
    def __init__(self, connection: _FakeAsyncpgConnection) -> None:
        self._connection = connection

    async def start(self) -> None:
        self._connection.check_open()

    async def commit(self) -> None:
        self._connection.check_open()

    async def rollback(self) -> None:
        self._connection.check_open()


class _FakeAsyncpgConnection:
    """Enough of ``asyncpg.Connection`` for SQLAlchemy's asyncpg dialect."""

    def __init__(self) -> None:
        self.closed = False
        self.terminated = False
        self.ping_entered = asyncio.Event()
        self.block_ping = False
        self.close_entered = asyncio.Event()
        self.hold_close = False
        self.connect_delay = 0.0

    def check_open(self) -> None:
        if self.closed:
            raise asyncpg.exceptions.InterfaceError("connection is closed")

    async def set_type_codec(self, *args, **kwargs) -> None:
        return None

    async def reload_schema_state(self) -> None:
        return None

    async def prepare(self, sql: str, **kwargs) -> _Statement:
        self.check_open()
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)
        return _Statement(sql)

    def transaction(self, **kwargs) -> _Transaction:
        return _Transaction(self)

    async def fetchrow(self, sql: str, *args):
        # SQLAlchemy's pool pre-ping runs ``fetchrow(";")`` at checkout.
        self.check_open()
        if self.block_ping:
            self.ping_entered.set()
            await asyncio.Event().wait()  # in flight until cancelled
        return None

    async def close(self, timeout: float | None = None) -> None:
        # Graceful close is a network round trip. An anyio cancel scope
        # cancels it again; asyncio.timeout / Task.cancel() cancel it once.
        self.close_entered.set()
        if self.hold_close:
            await asyncio.sleep(0.5)
        else:
            await asyncio.sleep(0.05)
        self.closed = True

    def terminate(self) -> None:
        self.closed = True
        self.terminated = True

    def is_closed(self) -> bool:
        return self.closed


@pytest.mark.asyncio
async def test_disconnect_during_pre_ping_returns_connection_to_pool(monkeypatch):
    connections: list[_FakeAsyncpgConnection] = []

    async def creator() -> _FakeAsyncpgConnection:
        connection = _FakeAsyncpgConnection()
        connections.append(connection)
        return connection

    engine = create_async_engine(
        "postgresql+asyncpg://user:pass@db.invalid/app",
        async_creator=creator,
        poolclass=PlannerQueuePool,
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
    )
    pool = engine.sync_engine.pool
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(uow_async, "AsyncSessionLocal", session_factory)

    try:
        # Warm one pooled connection, so the next checkout pre-pings it.
        async with uow_async.AsyncUnitOfWork.read_only() as uow:
            await uow.session.execute(text("SELECT 1"))
        assert pool.checkedout() == 0
        pooled = connections[0]
        pooled.block_ping = True

        async def body():
            async with uow_async.AsyncUnitOfWork() as uow:
                await uow.session.execute(text("SELECT 1"))
            yield b"data: never\n\n"

        sent: list[dict] = []
        received = 0

        async def receive() -> dict:
            nonlocal received
            received += 1
            if received == 1:
                return {"type": "http.request", "body": b"", "more_body": False}
            # The client hangs up while the stream is checking out a connection.
            await pooled.ping_entered.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "path": "/v1/chat/messages",
            "headers": [],
        }
        with anyio.fail_after(5):
            await StreamingResponse(body(), media_type="text/event-stream")(
                scope, receive, send
            )
        # Let the shielded session-close task from AsyncUnitOfWork finish.
        await asyncio.sleep(0.2)

        assert pooled.ping_entered.is_set()
        assert pooled.terminated
        # The request is still cancelled: the stream never got past checkout.
        assert not [m for m in sent if m.get("body") == b"data: never\n\n"]
        assert pool.checkedout() == 0, pool.status()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            gc.collect()
        assert not [w for w in caught if "non-checked-in connection" in str(w.message)]
        assert pool.checkedout() == 0
    finally:
        await engine.dispose()


async def _engine_due_for_recycle():
    """One pooled connection old enough that the next checkout recycles it.

    ``pool_recycle`` matches production (120s). The graceful close of the
    expired connection takes half a second, and a replacement connection's
    first prepare takes another half a second. A swallowed cancel therefore
    returns about 0.6s after ``asyncio.timeout(0.1)`` started.
    """

    connections: list[_FakeAsyncpgConnection] = []

    async def creator() -> _FakeAsyncpgConnection:
        connection = _FakeAsyncpgConnection()
        if connections:
            connection.connect_delay = 0.5
        connections.append(connection)
        return connection

    engine = create_async_engine(
        "postgresql+asyncpg://user:pass@db.invalid/app",
        async_creator=creator,
        poolclass=PlannerQueuePool,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=120,
    )
    pool = engine.sync_engine.pool
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    assert pool.checkedout() == 0
    record = pool._pool._queue._queue[0]
    record.starttime = time.time() - 121
    pooled = connections[0]
    pooled.hold_close = True
    return engine, pool, pooled


async def _checkout(engine) -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


@pytest.mark.asyncio
async def test_recycle_asyncio_timeout_raises_promptly():
    engine, pool, _pooled = await _engine_due_for_recycle()
    try:
        started = time.perf_counter()
        try:
            async with asyncio.timeout(0.1):
                await _checkout(engine)
        except TimeoutError:
            elapsed = time.perf_counter() - started
        else:
            elapsed = time.perf_counter() - started
            pytest.fail(
                "asyncio.timeout(0.1) returned normally after "
                f"{elapsed:.3f}s during pool recycle"
            )
        assert elapsed < 0.4, elapsed
        assert pool.checkedout() == 0, pool.status()
    finally:
        await asyncio.sleep(0.5)
        await engine.dispose()


@pytest.mark.asyncio
async def test_recycle_task_cancel_propagates():
    engine, pool, pooled = await _engine_due_for_recycle()
    try:
        task = asyncio.create_task(_checkout(engine))
        await pooled.close_entered.wait()
        started = time.perf_counter()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            elapsed = time.perf_counter() - started
        else:
            elapsed = time.perf_counter() - started
            pytest.fail(
                "task.cancel() was lost during pool recycle; "
                f"cancelled={task.cancelled()} cancelling={task.cancelling()} "
                f"elapsed={elapsed:.3f}s"
            )
        assert task.cancelled()
        assert task.cancelling() == 1
        assert elapsed < 0.4, elapsed
        assert pool.checkedout() == 0, pool.status()
    finally:
        await asyncio.sleep(0.5)
        await engine.dispose()
