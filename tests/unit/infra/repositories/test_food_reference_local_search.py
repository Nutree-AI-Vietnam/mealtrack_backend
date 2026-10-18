"""Statement shape for accent-insensitive local food search."""

from __future__ import annotations

import pytest
from sqlalchemy import Select, true
from sqlalchemy.dialects import postgresql

from src.domain.utils.food_search_text import MAX_FOOD_SEARCH_QUERY_LENGTH
from src.infra.repositories.food_reference_local_search import (
    build_local_search_statement,
    search_regions,
)


def _sql(statement: Select | None) -> str:
    assert statement is not None
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    # The default driver's paramstyle doubles literal percent signs.
    return compiled.replace("%%", "%")


def _where(sql: str) -> str:
    return sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]


def _order(sql: str) -> str:
    return sql.split("ORDER BY", 1)[1]


@pytest.mark.parametrize("query", ["", "   ", None, "!!!", "--"])
def test_queries_without_words_build_nothing(query: str | None) -> None:
    assert build_local_search_statement(query, "VN", true()) is None


def test_requested_region_is_searched_with_the_global_catalog() -> None:
    assert search_regions("VN") == ["VN", "global"]
    assert search_regions("global") == ["global"]

    where = _where(_sql(build_local_search_statement("rice", "VN", true())))

    assert "food_reference.region IN ('VN', 'global')" in where


def test_quarantined_rows_stay_hidden_whatever_the_eligibility_mode() -> None:
    where = _where(_sql(build_local_search_statement("rice", "US", true())))

    assert "food_reference.integrity_status != 'quarantined'" in where


def test_words_match_the_folded_column_on_its_trigram_index() -> None:
    where = _where(_sql(build_local_search_statement("Phở Bò tái", "VN", true())))

    assert "food_reference.name_search LIKE '%pho%'" in where
    assert "food_reference.name_search LIKE '%tai%'" in where
    # Two-letter words only match at a word start, so "bò" cannot hit "jumbo".
    assert "food_reference.name_search LIKE '% bo%'" in where
    assert "'%bo%'" not in where
    assert "name_normalized" not in where


def test_preparation_qualifiers_do_not_filter_results() -> None:
    where = _where(_sql(build_local_search_statement("grilled chicken", "US", true())))

    assert "'%chicken%'" in where
    assert "grilled" not in where


def test_ranking_prefers_whole_phrase_then_similarity() -> None:
    order = _order(_sql(build_local_search_statement("egg", "US", true())))

    assert "LIKE '% egg %'" in order
    assert "LIKE '% eggs %'" in order
    assert "LIKE '% egg%'" in order
    assert "similarity(food_reference.name, 'egg')" in order
    assert "similarity(food_reference.name_search, 'egg')" in order
    assert order.rstrip().endswith("food_reference.id ASC")


def test_accented_queries_rank_exact_diacritics_first() -> None:
    accented = _order(_sql(build_local_search_statement("bơ", "VN", true())))
    plain = _order(_sql(build_local_search_statement("bo", "VN", true())))

    assert accented.lstrip().startswith("CASE WHEN ((coalesce(food_reference.name_vi")
    assert "ILIKE '%' || 'bơ' || '%'" in accented
    assert "ILIKE" not in plain


def test_provider_identity_keys_are_looked_up_exactly() -> None:
    sql = _sql(build_local_search_statement("FatSecret:33890", "US", true()))
    where = _where(sql)

    assert "lower(food_reference.name_normalized) = 'fatsecret:33890'" in where
    assert "name_search" not in where
    assert "food_reference.integrity_status != 'quarantined'" in where


def test_long_pasted_text_is_searched_by_its_start() -> None:
    # Accented text also exercises the exact-diacritics ranking pattern.
    pasted = "phở bò tái chín " * 100
    start = pasted[:MAX_FOOD_SEARCH_QUERY_LENGTH]

    assert _sql(build_local_search_statement(pasted, "VN", true())) == _sql(
        build_local_search_statement(start, "VN", true())
    )


def test_eligibility_clause_is_always_applied() -> None:
    from sqlalchemy import false

    where = _where(_sql(build_local_search_statement("rice", "US", false())))

    assert "false" in where.lower()
