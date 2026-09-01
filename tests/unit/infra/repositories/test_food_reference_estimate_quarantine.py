from unittest.mock import AsyncMock

import pytest

from src.infra.repositories.food_reference_locale import (
    FoodReferenceLocaleRepository,
)
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def unique(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def scalars(self):
        return _Scalars(self._rows)

    def all(self):
        return self._rows


class _AsyncSession:
    def __init__(self):
        self.statement = None
        self.flush = AsyncMock()

    async def execute(self, statement):
        self.statement = statement
        return _Result([])


@pytest.mark.asyncio
async def test_search_by_name_excludes_ai_estimate():
    session = _AsyncSession()
    repo = AsyncFoodReferenceRepository(session)

    await repo.search_by_name("Rice", region="global")

    compiled_sql = str(session.statement)
    assert (
        "source_namespace !=" in compiled_sql
        or "source_namespace IS NULL" in compiled_sql
    )
    assert "source !=" in compiled_sql


@pytest.mark.asyncio
async def test_search_local_excludes_ai_estimate():
    session = _AsyncSession()
    repo = AsyncFoodReferenceRepository(session)

    await repo.search_local("Rice", region="global", limit=10)

    compiled_sql = str(session.statement)
    assert (
        "source_namespace !=" in compiled_sql
        or "source_namespace IS NULL" in compiled_sql
    )
    assert "source !=" in compiled_sql


@pytest.mark.asyncio
async def test_list_catalog_seed_candidates_excludes_ai_estimate():
    session = _AsyncSession()
    repo = AsyncFoodReferenceRepository(session)

    await repo.list_catalog_seed_candidates()

    compiled_sql = str(session.statement)
    assert (
        "source_namespace !=" in compiled_sql
        or "source_namespace IS NULL" in compiled_sql
    )
    assert "source !=" in compiled_sql


@pytest.mark.asyncio
async def test_find_by_locale_names_excludes_ai_estimate():
    session = _AsyncSession()
    repo = FoodReferenceLocaleRepository(session)

    await repo.find_by_locale_names("en", ["Rice", "Egg"])

    compiled_sql = str(session.statement)
    assert (
        "source_namespace !=" in compiled_sql
        or "source_namespace IS NULL" in compiled_sql
    )
    assert "source !=" in compiled_sql


@pytest.mark.asyncio
async def test_find_batch_by_normalized_names_excludes_ai_estimate():
    session = _AsyncSession()
    repo = AsyncFoodReferenceRepository(session)

    await repo.find_batch_by_normalized_names(["rice", "chicken breast"])

    compiled_sql = str(session.statement)
    assert (
        "source_namespace !=" in compiled_sql
        or "source_namespace IS NULL" in compiled_sql
    )
    assert "source !=" in compiled_sql


@pytest.mark.asyncio
async def test_find_by_normalized_name_excludes_ai_estimate():
    session = _AsyncSession()
    repo = AsyncFoodReferenceRepository(session)

    await repo.find_by_normalized_name("rice")

    compiled_sql = str(session.statement)
    assert (
        "source_namespace !=" in compiled_sql
        or "source_namespace IS NULL" in compiled_sql
    )
    assert "source !=" in compiled_sql
