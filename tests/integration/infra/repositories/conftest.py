"""Fixtures for pgvector and async repository integration tests.

These tests require a real Postgres DB with the vector extension installed.
They connect directly using DATABASE_URL from the environment (Neon Postgres).
Each test runs in an isolated transaction that is rolled back after.

We override root conftest fixtures that assume MySQL to prevent interference.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import sessionmaker, Session


def _make_pg_engine():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping pgvector integration tests")

    # Normalise to psycopg2 driver
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    # Neon requires SSL. Local Postgres usually does not.
    host_is_local = any(token in url for token in ("localhost", "127.0.0.1"))
    if "sslmode" not in url and not host_is_local:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"

    return create_engine(url, echo=False, pool_pre_ping=True)


@pytest.fixture(scope="module")
def pg_engine():
    engine = _make_pg_engine()
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(pg_engine) -> Session:
    """Real Postgres session — rolled back after each test."""
    connection = pg_engine.connect()
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    connection.commit()

    transaction = connection.begin()
    factory = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        try:
            transaction.rollback()
        except Exception:
            pass
        connection.close()


# Override root conftest autouse fixtures so they don't try to create MySQL schemas
@pytest.fixture(autouse=True)
def mock_scoped_session(db_session):
    """No-op override — pgvector tests manage their own session."""
    yield


@pytest.fixture(scope="session")
def test_engine(pg_engine):
    """Override root test_engine to use Postgres instead of MySQL."""
    return pg_engine


@pytest.fixture()
def test_session(db_session):
    """Override root test_session to use our real Postgres session."""
    return db_session


# ---------------------------------------------------------------------------
# Async fixtures (asyncpg) — used by test_meal_repository_async.py
# ---------------------------------------------------------------------------


def _make_async_pg_url() -> str:
    url = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL", "")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping async integration tests")

    for old, new in [
        ("postgres://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
    ]:
        if url.startswith(old):
            url = url.replace(old, new, 1)
            break

    if "postgresql+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url.split("://", 1)[1]

    return url


@pytest_asyncio.fixture()
async def async_pg_engine():
    """Function-scoped engine — avoids event-loop mismatch in asyncio_mode=strict."""
    engine = create_async_engine(_make_async_pg_url(), echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def async_db_session(async_pg_engine) -> AsyncSession:
    """Real Postgres async session — rolled back after each test."""
    async with async_pg_engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()  # savepoint — absorbs session.commit() calls
        factory = async_sessionmaker(
            bind=conn,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        try:
            yield session
        finally:
            await session.close()
            try:
                await conn.rollback()
            except Exception:
                pass
