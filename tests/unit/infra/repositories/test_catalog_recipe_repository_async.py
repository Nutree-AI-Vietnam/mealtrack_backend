from unittest.mock import MagicMock

import pytest

from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def unique(self):
        return self

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def one(self):
        return self._one

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._one


class _AsyncSession:
    def __init__(self, results):
        self._results = list(results)
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return self._results.pop(0)


def _meal_row():
    food_reference = MagicMock()
    food_reference.id = 7
    food_reference.name = "Rice"
    food_reference.source = "catalog_seed"
    food_reference.is_verified = True
    food_reference.protein_100g = 2.7
    food_reference.carbs_100g = 28.0
    food_reference.fat_100g = 0.3
    food_reference.fiber_100g = 0.4
    food_reference.sugar_100g = 0.1
    food_reference.density = 1.0
    food_reference.serving_size_rows = []

    ingredient = MagicMock()
    ingredient.food_reference_id = 7
    ingredient.display_name = "Rice"
    ingredient.quantity = 100
    ingredient.unit = "g"
    ingredient.food_reference = food_reference

    row = MagicMock()
    row.id = "catalog-1"
    row.catalog_key = "vn-rice"
    row.content_hash = "a" * 64
    row.name = "Rice Bowl"
    row.cuisine = "vietnamese"
    row.description = None
    row.image_url = None
    row.breakfast_eligible = True
    row.lunch_eligible = False
    row.dinner_eligible = False
    row.snack_eligible = False
    row.is_active = True
    row.popularity_rank = 1
    row.ingredients = [ingredient]
    return row


@pytest.mark.asyncio
async def test_list_active_meals_filters_by_cuisine_and_meal_type():
    session = _AsyncSession([_Result(rows=[_meal_row()])])
    repo = AsyncCatalogMealRepository(session)

    result = await repo.list_active_meals(
        cuisine="vietnamese",
        meal_type="breakfast",
    )

    assert len(result) == 1
    assert result[0].catalog_key == "vn-rice"
    assert result[0].meal_types == ("breakfast",)
    assert result[0].ingredients[0].food_reference_id == 7
    statement = str(session.statement)
    assert "meal_catalog.is_active" in statement
    assert "meal_catalog.cuisine" in statement
    assert "meal_catalog.breakfast_eligible" in statement


@pytest.mark.asyncio
async def test_list_popular_page_orders_by_rank_and_skips_snapshot_scan():
    session = _AsyncSession([_Result(one=(True, 1, 0)), _Result(rows=[_meal_row()])])
    repo = AsyncCatalogMealRepository(session)

    page = await repo.list_popular_page(
        limit=20,
        offset=0,
        query="rice",
        cuisine="vietnamese",
        meal_type="breakfast",
    )

    assert page.total == 1
    assert page.any_ranked is True
    assert page.unranked_count == 0
    assert page.items[0].id == "catalog-1"
    statement = str(session.statement)
    assert "meal_catalog.popularity_rank" in statement
    assert "LIMIT" in statement.upper() or "limit" in statement


@pytest.mark.asyncio
async def test_list_popular_page_orders_by_shuffle_seed_when_provided():
    session = _AsyncSession([_Result(one=(True, 1, 0)), _Result(rows=[_meal_row()])])
    repo = AsyncCatalogMealRepository(session)

    page = await repo.list_popular_page(
        limit=20,
        offset=0,
        shuffle_seed="refresh-a",
    )

    assert page.items[0].id == "catalog-1"
    statement = str(session.statement)
    assert "md5" in statement.lower()


@pytest.mark.asyncio
async def test_get_active_catalog_revision_includes_imaged_count():
    session = _AsyncSession(
        [_Result(one=(180, None, None, 100))],
    )
    repo = AsyncCatalogMealRepository(session)

    revision = await repo.get_active_catalog_revision()

    assert revision.active_count == 180
    assert revision.imaged_count == 100
    statement = str(session.statement)
    assert "meal_catalog.image_url" in statement


@pytest.mark.asyncio
async def test_get_meal_scopes_to_active_catalog_row():
    session = _AsyncSession([_Result(one=_meal_row())])
    repo = AsyncCatalogMealRepository(session)

    result = await repo.get_meal("catalog-1")

    assert result is not None
    assert result.id == "catalog-1"
    assert result.calories > 0
    statement = str(session.statement)
    assert "meal_catalog.id" in statement
    assert "meal_catalog.is_active" in statement
