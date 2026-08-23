from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.infra.repositories.food_reference_locale import FoodReferenceLocaleRepository
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)


class _Result:
    def __init__(self, rows=None, all_rows=None):
        self._rows = rows if rows is not None else []
        self._all_rows = all_rows if all_rows is not None else self._rows

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return self._all_rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, results):
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self._results.pop(0)


def _food_model(*, food_id=7, name="Beef", name_vi=None):
    row = MagicMock()
    row.id = food_id
    row.name = name
    row.name_vi = name_vi
    row.brand = None
    row.barcode = None
    row.category = None
    row.region = "global"
    row.fdc_id = None
    row.protein_100g = 26.0
    row.carbs_100g = 0.0
    row.fat_100g = 15.0
    row.fiber_100g = 0.0
    row.sugar_100g = 0.0
    row.serving_size_rows = []
    row.serving_sizes = None
    row.density = 1.0
    row.serving_size = None
    row.nutrient_rows = []
    row.extra_nutrients = None
    row.source = "fatsecret"
    row.source_namespace = "fatsecret"
    row.source_food_id = "33890"
    row.is_verified = True
    row.image_url = None
    row.name_normalized = "fatsecret:33890"
    return row


@pytest.mark.asyncio
async def test_find_by_locale_names_matches_english_name_case_insensitive():
    model = _food_model(name="Beef, ground")
    session = _Session([_Result(rows=[model])])
    repo = FoodReferenceLocaleRepository(session)

    result = await repo.find_by_locale_names("en", ["beef, GROUND"])

    assert result["beef, GROUND"]["id"] == 7


@pytest.mark.asyncio
async def test_find_by_locale_names_matches_name_vi_column():
    model = _food_model(name_vi="Thit bo")
    session = _Session([_Result(rows=[model])])
    repo = FoodReferenceLocaleRepository(session)

    result = await repo.find_by_locale_names("vi", ["thit bo"])

    assert result["thit bo"]["id"] == 7


@pytest.mark.asyncio
async def test_find_by_locale_names_requires_exact_match_not_substring():
    session = _Session([_Result(rows=[])])
    repo = FoodReferenceLocaleRepository(session)

    result = await repo.find_by_locale_names("en", ["Beef"])

    assert result == {}


@pytest.mark.asyncio
async def test_find_by_locale_names_returns_empty_for_blank_input():
    session = _Session([])
    repo = FoodReferenceLocaleRepository(session)

    result = await repo.find_by_locale_names("en", ["   ", ""])

    assert result == {}
    assert session.statements == []


@pytest.mark.asyncio
async def test_get_display_projections_returns_name_and_name_vi():
    rows = [
        SimpleNamespace(id=7, name="Beef", name_vi="Thit bo"),
        SimpleNamespace(id=8, name="Rice", name_vi=None),
    ]
    session = _Session(
        [_Result(all_rows=rows), _Result(all_rows=[])]
    )
    repo = FoodReferenceLocaleRepository(session)

    result = await repo.get_display_projections([7, 8])

    assert result[7] == {
        "name": "Beef",
        "name_vi": "Thit bo",
        "serving_labels": {},
    }
    assert result[8] == {"name": "Rice", "name_vi": None, "serving_labels": {}}


@pytest.mark.asyncio
async def test_get_display_projections_includes_serving_name_vi():
    from src.app.services.serving_label import serving_phrase_key

    rows = [SimpleNamespace(id=7, name="Rice", name_vi="Cơm")]
    serving_rows = [
        (7, "cup, cooked, diced", "cốc, đã nấu, thái hạt lựu"),
    ]
    session = _Session(
        [_Result(all_rows=rows), _Result(all_rows=serving_rows)]
    )
    repo = FoodReferenceLocaleRepository(session)

    result = await repo.get_display_projections([7], language="vi")

    assert result[7]["serving_labels"][
        serving_phrase_key("cup, cooked, diced")
    ] == "cốc, đã nấu, thái hạt lựu"
    assert serving_phrase_key("g") not in result[7]["serving_labels"]


@pytest.mark.asyncio
async def test_get_display_projections_returns_empty_for_no_ids():
    session = _Session([])
    repo = FoodReferenceLocaleRepository(session)

    result = await repo.get_display_projections([])

    assert result == {}
    assert session.statements == []


class _SearchResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _SearchSession:
    def __init__(self, results):
        self._results = list(results)
        self.statement = None
        self.flush = MagicMock()

    async def execute(self, statement):
        self.statement = statement
        return self._results.pop(0)


def _search_row(*, food_id, name, name_normalized, name_vi=None):
    row = MagicMock()
    row.id = food_id
    row.name = name
    row.name_vi = name_vi
    row.name_normalized = name_normalized
    row.brand = None
    row.barcode = None
    row.category = None
    row.region = "global"
    row.fdc_id = None
    row.protein_100g = 26.0
    row.carbs_100g = 0.0
    row.fat_100g = 15.0
    row.fiber_100g = 0.0
    row.sugar_100g = 0.0
    row.serving_size_rows = []
    row.serving_sizes = None
    row.density = 1.0
    row.serving_size = None
    row.nutrient_rows = []
    row.extra_nutrients = None
    row.source = "fatsecret"
    row.source_namespace = "fatsecret"
    row.source_food_id = "33890"
    row.is_verified = True
    row.image_url = None
    return row


@pytest.mark.asyncio
async def test_search_local_finds_identity_keyed_row_by_display_name():
    row = _search_row(food_id=7, name="Beef, ground", name_normalized="fatsecret:33890")
    session = _SearchSession([_SearchResult([row])])
    repo = AsyncFoodReferenceRepository(session)

    result = await repo.search_local("beef", "US", 10)

    assert [item.id for item in result] == [7]
    statement = str(session.statement)
    assert "food_reference_translation" not in statement
    assert "name_vi" in statement


@pytest.mark.asyncio
async def test_search_local_does_not_substring_match_identity_key_by_typed_text():
    session = _SearchSession([_SearchResult([])])
    repo = AsyncFoodReferenceRepository(session)

    result = await repo.search_local("33890", "US", 10)

    assert result == []
    statement = str(session.statement)
    assert "not like" in statement.lower()


@pytest.mark.asyncio
async def test_search_local_matches_exact_identity_key_when_query_is_that_key():
    row = _search_row(food_id=7, name="Beef, ground", name_normalized="fatsecret:33890")
    session = _SearchSession([_SearchResult([row])])
    repo = AsyncFoodReferenceRepository(session)

    result = await repo.search_local("fatsecret:33890", "US", 10)

    assert [item.id for item in result] == [7]
