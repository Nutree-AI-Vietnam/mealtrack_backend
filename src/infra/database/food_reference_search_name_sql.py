"""SQL for the accent-folded ``food_reference.name_search`` generated column."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement

from src.domain.utils.food_search_text import (
    FOLD_DELETED_CHARS,
    FOLD_SOURCE_CHARS,
    FOLD_TARGET_CHARS,
)


def folded_search_sql(text_sql: str) -> str:
    """Postgres expression mirroring ``fold_food_search_text`` of ``text_sql``.

    ``translate`` drops characters listed past the end of its target string,
    which removes combining marks. Every function used is immutable, as a
    stored generated column requires; no locale-dependent ``lower()``.
    """
    source = FOLD_SOURCE_CHARS + FOLD_DELETED_CHARS
    return (
        "(' ' || btrim(regexp_replace(translate("
        f"{text_sql}, "
        f"'{source}', '{FOLD_TARGET_CHARS}'), "
        "'[^a-z0-9]+', ' ', 'g')))"
    )


def food_reference_search_name_postgresql_sql() -> str:
    """Postgres expression mirroring ``fold_food_search_text(name_vi + name)``."""
    return folded_search_sql("coalesce(name_vi, '') || ' ' || name")


class FoodReferenceSearchName(ColumnElement[str]):
    """Dialect-aware generation expression for ``name_search``."""

    __visit_name__ = "food_reference_search_name"
    type = Text()
    inherit_cache = True


@compiles(FoodReferenceSearchName)
def _compile_search_name_default(
    element: FoodReferenceSearchName, compiler: Any, **kw: Any
) -> str:
    # SQLite (unit tests) has neither translate() nor regexp_replace();
    # lower-casing keeps ASCII catalogs searchable there.
    return "(' ' || lower(coalesce(name_vi, '') || ' ' || name))"


@compiles(FoodReferenceSearchName, "postgresql")
def _compile_search_name_postgresql(
    element: FoodReferenceSearchName, compiler: Any, **kw: Any
) -> str:
    return food_reference_search_name_postgresql_sql()
