"""PostgreSQL fixtures for catalog production-gate tests."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _sync_url(async_url: str) -> str:
    return async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("PostgreSQL integration tests require TEST_DATABASE_URL")
    return url


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> Iterator[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = _sync_url(test_database_url)
    env["DATABASE_URL_DIRECT"] = _sync_url(test_database_url)
    subprocess.run([sys.executable, "scripts/init_postgres_db.py"], check=True, env=env)
    yield test_database_url


@pytest_asyncio.fixture
async def async_session_factory(
    migrated_database: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_database, echo=False)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        await _truncate_test_tables(session)
        yield session
        await session.rollback()
        await _truncate_test_tables(session)


async def _truncate_test_tables(session: AsyncSession) -> None:
    await session.execute(
        text(
            """
            TRUNCATE TABLE
              meal_recommendation_operations,
              meal_recommendations,
              meal_catalog_ingredients,
              meal_catalog,
              food_reference,
              food_item,
              nutrition,
              meal,
              mealimage,
              users
            RESTART IDENTITY CASCADE
            """
        )
    )
    await session.commit()
