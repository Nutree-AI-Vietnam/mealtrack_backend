"""A request cancelled mid-flight must return its pooled connection (PYTHON-JZ).

Starlette cancels a ``StreamingResponse`` through an anyio cancel scope when
the client disconnects. anyio keeps re-cancelling every await in that scope.
If the cancel lands while the pool pre-pings a connection at checkout,
SQLAlchemy invalidates the connection, and the asyncpg ``terminate()`` it
runs is cancelled again. That second ``CancelledError`` escaped before the
connection record was checked in, so the slot stayed checked out until the
garbage collector found it ("non-checked-in connection").

No database is needed: the fake below stands in for ``asyncpg.Connection``
behind SQLAlchemy's real asyncpg dialect, ``PlannerQueuePool`` and
``AsyncUnitOfWork``.
"""

from __future__ import annotations

import asyncio
import gc
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

    def check_open(self) -> None:
        if self.closed:
            raise asyncpg.exceptions.InterfaceError("connection is closed")

    async def set_type_codec(self, *args, **kwargs) -> None:
        return None

    async def reload_schema_state(self) -> None:
        return None

    async def prepare(self, sql: str, **kwargs) -> _Statement:
        self.check_open()
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
        # Graceful close is a network round trip; anyio cancels it again.
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
