"""SQL for the accent-folded ``supplement_products.name_search`` generated column."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.elements import ColumnElement

from src.infra.database.food_reference_search_name_sql import folded_search_sql

_TEXT = "coalesce(brand, '') || ' ' || name || ' ' || coalesce(name_original, '')"


def supplement_search_name_postgresql_sql() -> str:
    """Brand, name and printed name folded the way food search folds a query."""
    return folded_search_sql(_TEXT)


class SupplementSearchName(ColumnElement[str]):
    """Dialect-aware generation expression for ``name_search``."""

    __visit_name__ = "supplement_search_name"
    type = Text()
    inherit_cache = True


@compiles(SupplementSearchName)
def _compile_default(element: SupplementSearchName, compiler: Any, **kw: Any) -> str:
    # SQLite (unit tests) has neither translate() nor regexp_replace().
    return f"(' ' || lower({_TEXT}))"


@compiles(SupplementSearchName, "postgresql")
def _compile_postgresql(element: SupplementSearchName, compiler: Any, **kw: Any) -> str:
    return supplement_search_name_postgresql_sql()
