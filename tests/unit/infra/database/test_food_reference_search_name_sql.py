"""The stored ``name_search`` expression must keep matching query folding."""

import hashlib
import re
import unicodedata

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from src.domain.utils.food_search_text import (
    FOLD_DELETED_CHARS,
    FOLD_SOURCE_CHARS,
    FOLD_TARGET_CHARS,
    fold_food_search_text,
)
from src.infra.database.food_reference_search_name_sql import (
    FoodReferenceSearchName,
    food_reference_search_name_postgresql_sql,
)

# Rows already stored keep the expression they were generated with. If this
# hash changes, add a migration that drops and re-adds
# food_reference.name_search (and its trigram index) so existing rows are
# folded the same way as new queries, then update the hash.
_STORED_EXPRESSION_SHA256 = (
    "1c324e0dae70fbcb35c61dfbcac9606af9ccad4017d0930ef57e434a4c8908f0"
)


def _postgres_translate(text: str, source: str, target: str) -> str:
    """Postgres ``translate``: first match wins; no target char deletes."""
    out: list[str] = []
    for char in text:
        index = source.find(char)
        if index < 0:
            out.append(char)
        elif index < len(target):
            out.append(target[index])
    return "".join(out)


def _stored_name_search(name: str, name_vi: str | None) -> str:
    translated = _postgres_translate(
        f"{name_vi or ''} {name}",
        FOLD_SOURCE_CHARS + FOLD_DELETED_CHARS,
        FOLD_TARGET_CHARS,
    )
    return " " + re.sub(r"[^a-z0-9]+", " ", translated).strip(" ")


def test_stored_expression_is_unchanged_since_its_migration() -> None:
    digest = hashlib.sha256(
        food_reference_search_name_postgresql_sql().encode()
    ).hexdigest()

    assert digest == _STORED_EXPRESSION_SHA256, (
        "food_reference.name_search expression changed; existing rows keep the "
        "old folding until a new migration regenerates the column"
    )


@pytest.mark.parametrize(
    ("name", "name_vi"),
    [
        ("Beef pho", "Phở bò"),
        ("Tofu", "Đậu phụ"),
        ("Broken rice with pork chop", "Cơm tấm sườn"),
        ("Creme brulee", None),
        ("CHICKEN BREAST, grilled (100g)", ""),
        ("Pork", "Thịt heo"),
    ],
)
def test_stored_expression_folds_like_queries(name: str, name_vi: str | None) -> None:
    assert _stored_name_search(name, name_vi) == fold_food_search_text(
        f"{name_vi or ''} {name}"
    )


def test_stored_expression_folds_decomposed_vietnamese() -> None:
    decomposed = unicodedata.normalize("NFD", "Bún bò Huế")

    assert _stored_name_search("Hue beef noodle soup", decomposed) == (
        " bun bo hue hue beef noodle soup"
    )


def test_column_compiles_per_dialect() -> None:
    element = FoodReferenceSearchName()

    compiled_pg = str(element.compile(dialect=postgresql.dialect()))
    compiled_sqlite = str(element.compile(dialect=sqlite.dialect()))

    assert compiled_pg == food_reference_search_name_postgresql_sql()
    assert "translate(" not in compiled_sqlite
    assert "lower(" in compiled_sqlite
