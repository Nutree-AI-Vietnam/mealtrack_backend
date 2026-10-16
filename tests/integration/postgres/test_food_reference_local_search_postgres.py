"""Local food search on PostgreSQL: accent folding, regions and relevance."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.database.models.food_reference_model import FoodReferenceModel
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)

pytestmark = pytest.mark.integration

# (name, name_vi, region, integrity_status)
_CATALOG: tuple[tuple[str, str | None, str, str], ...] = (
    ("Beef pho", "Phở bò", "VN", "unknown"),
    ("Chicken pho", "Phở gà", "VN", "unknown"),
    ("Hue beef noodle soup", "Bún bò Huế", "VN", "unknown"),
    ("Vietnamese beef stew", "Bò kho", "VN", "unknown"),
    ("Beef", "Thịt bò", "VN", "unknown"),
    ("Avocado", "Bơ", "VN", "unknown"),
    ("Broken rice with grilled pork", "Cơm tấm", "VN", "unknown"),
    ("Chicken rice", "Cơm gà", "VN", "unknown"),
    ("Vietnamese baguette sandwich", "Bánh mì", "VN", "unknown"),
    ("Steamed rice rolls", "Bánh cuốn", "VN", "unknown"),
    ("Fried chicken", "Gà rán", "VN", "unknown"),
    ("Chicken breast, grilled", None, "US", "unknown"),
    ("Chicken thigh", None, "US", "unknown"),
    ("Chicken noodle soup", None, "US", "unknown"),
    ("Chickpeas", None, "US", "unknown"),
    ("Fried rice", None, "US", "unknown"),
    ("Brown rice", None, "US", "unknown"),
    ("Rice, white", None, "US", "quarantined"),
    ("Rice noodles", None, "global", "unknown"),
    ("Egg, boiled", None, "US", "unknown"),
    ("Eggs, scrambled", None, "US", "unknown"),
    ("Eggplant", None, "US", "unknown"),
    ("Veggie burger", None, "US", "unknown"),
    ("Jumbo shrimp", None, "global", "unknown"),
    ("Banana", None, "US", "unknown"),
    ("Banana bread", None, "US", "unknown"),
)

# Expected first result per query, English and Vietnamese, typed with and
# without accents.
_GOLDEN_TOP_RESULTS: tuple[tuple[str, str, str], ...] = (
    ("chicken breast", "US", "Chicken breast, grilled"),
    ("fried rice", "US", "Fried rice"),
    ("banana", "US", "Banana"),
    ("egg", "US", "Egg, boiled"),
    ("phở bò", "VN", "Beef pho"),
    ("pho ga", "VN", "Chicken pho"),
    ("bun bo hue", "VN", "Hue beef noodle soup"),
    ("bánh mì", "VN", "Vietnamese baguette sandwich"),
    ("com tam", "VN", "Broken rice with grilled pork"),
    ("bơ", "VN", "Avocado"),
    ("beef", "VN", "Beef"),
)


def _food(
    normalized: str,
    name: str,
    name_vi: str | None,
    region: str,
    integrity_status: str,
) -> FoodReferenceModel:
    return FoodReferenceModel(
        name=name,
        name_normalized=normalized,
        name_vi=name_vi,
        region=region,
        integrity_status=integrity_status,
        protein_100g=10.0,
        carbs_100g=20.0,
        fat_100g=5.0,
        fiber_100g=1.0,
        sugar_100g=1.0,
        density=1.0,
        source="catalog_seed",
        is_verified=True,
    )


@pytest_asyncio.fixture
async def repository(pg_session: AsyncSession) -> AsyncFoodReferenceRepository:
    pg_session.add_all(
        _food(f"search-seed-{index}", *row) for index, row in enumerate(_CATALOG)
    )
    pg_session.add(_food("fatsecret:33890", "Greek yogurt", None, "US", "unknown"))
    await pg_session.commit()
    return AsyncFoodReferenceRepository(pg_session)


async def _names(
    repository: AsyncFoodReferenceRepository,
    query: str,
    region: str = "VN",
    limit: int = 10,
) -> list[str]:
    results = await repository.search_local(query, region, limit)
    return [result.name for result in results]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["pho bo", "phở bò", "PHO BO", "Phở Bò", "pho bò"])
async def test_accents_and_case_do_not_change_matches(
    repository: AsyncFoodReferenceRepository, query: str
) -> None:
    assert await _names(repository, query) == ["Beef pho"]


@pytest.mark.asyncio
async def test_typed_accents_rank_exact_diacritics_first(
    repository: AsyncFoodReferenceRepository,
) -> None:
    avocado_first = await _names(repository, "bơ")
    beef_first = await _names(repository, "bò")

    assert avocado_first[0] == "Avocado"
    assert set(beef_first[:4]) == {
        "Beef pho",
        "Hue beef noodle soup",
        "Vietnamese beef stew",
        "Beef",
    }
    assert beef_first[-1] == "Avocado"


@pytest.mark.asyncio
async def test_short_words_only_match_at_a_word_start(
    repository: AsyncFoodReferenceRepository,
) -> None:
    names = await _names(repository, "bo")

    assert "Avocado" in names
    assert "Jumbo shrimp" not in names


@pytest.mark.asyncio
async def test_whole_words_and_plurals_outrank_longer_words(
    repository: AsyncFoodReferenceRepository,
) -> None:
    names = await _names(repository, "egg", region="US")

    assert set(names[:2]) == {"Egg, boiled", "Eggs, scrambled"}
    assert names[2:] == ["Eggplant", "Veggie burger"]


@pytest.mark.asyncio
async def test_quarantined_rows_are_never_returned(
    repository: AsyncFoodReferenceRepository,
) -> None:
    names = await _names(repository, "rice", region="US")

    assert "Rice, white" not in names
    assert {"Fried rice", "Brown rice", "Rice noodles"} <= set(names)


@pytest.mark.asyncio
async def test_requested_region_and_global_catalog_are_searched(
    repository: AsyncFoodReferenceRepository,
) -> None:
    vietnam = await _names(repository, "rice", region="VN")
    united_states = await _names(repository, "rice", region="US")

    assert set(vietnam) == {
        "Broken rice with grilled pork",
        "Chicken rice",
        "Steamed rice rolls",
        "Rice noodles",
    }
    assert set(united_states) == {"Fried rice", "Brown rice", "Rice noodles"}


@pytest.mark.asyncio
async def test_provider_identity_keys_find_only_their_row(
    repository: AsyncFoodReferenceRepository,
) -> None:
    assert await _names(repository, "FatSecret:33890", region="US") == ["Greek yogurt"]


@pytest.mark.asyncio
async def test_limit_caps_results(repository: AsyncFoodReferenceRepository) -> None:
    assert len(await _names(repository, "chicken", region="VN", limit=2)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "region", "expected"), _GOLDEN_TOP_RESULTS)
async def test_golden_queries_rank_the_expected_food_first(
    repository: AsyncFoodReferenceRepository,
    query: str,
    region: str,
    expected: str,
) -> None:
    names = await _names(repository, query, region=region, limit=5)

    assert names[:1] == [expected]
