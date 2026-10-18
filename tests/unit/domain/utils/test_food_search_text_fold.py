"""Accent-insensitive folding shared by search queries and catalog rows."""

import unicodedata

import pytest

from src.domain.utils.food_search_text import (
    FOLD_DELETED_CHARS,
    FOLD_SOURCE_CHARS,
    FOLD_TARGET_CHARS,
    fold_food_search_text,
    food_search_words,
)


@pytest.mark.parametrize(
    ("raw", "folded"),
    [
        ("Phở bò", " pho bo"),
        ("pho bo", " pho bo"),
        ("PHO BO", " pho bo"),
        ("Đậu phụ", " dau phu"),
        ("ỨC GÀ", " uc ga"),
        ("Bánh mì thịt", " banh mi thit"),
        ("Crème Brûlée", " creme brulee"),
        ("Chicken, breast (grilled) 100g", " chicken breast grilled 100g"),
        ("  extra   spaces  ", " extra spaces"),
    ],
)
def test_fold_matches_accented_and_plain_spellings(raw: str, folded: str) -> None:
    assert fold_food_search_text(raw) == folded


def test_decomposed_input_folds_like_precomposed() -> None:
    precomposed = "Cơm tấm sườn"
    decomposed = unicodedata.normalize("NFD", precomposed)

    assert decomposed != precomposed
    assert fold_food_search_text(decomposed) == fold_food_search_text(precomposed)
    assert fold_food_search_text(decomposed) == " com tam suon"


@pytest.mark.parametrize("raw", ["", None, "   ", "!!!"])
def test_empty_or_symbol_only_input_folds_to_single_space(raw: str | None) -> None:
    assert fold_food_search_text(raw) == " "
    assert food_search_words(raw) == []


def test_words_keep_query_order() -> None:
    assert food_search_words("Bún bò Huế") == ["bun", "bo", "hue"]


def test_fold_table_is_safe_to_embed_in_sql_literal() -> None:
    # The table is inlined into a quoted Postgres literal in the generated
    # column expression; quotes or backslashes would break it.
    combined = FOLD_SOURCE_CHARS + FOLD_TARGET_CHARS + FOLD_DELETED_CHARS
    assert "'" not in combined
    assert "\\" not in combined
    assert len(FOLD_SOURCE_CHARS) == len(FOLD_TARGET_CHARS)
    assert len(set(FOLD_SOURCE_CHARS + FOLD_DELETED_CHARS)) == len(
        FOLD_SOURCE_CHARS + FOLD_DELETED_CHARS
    )


def test_fold_output_alphabet_is_lowercase_ascii_words() -> None:
    sample = "".join(chr(code_point) for code_point in range(0x20, 0x2000))
    folded = fold_food_search_text(sample)

    assert folded.startswith(" ")
    assert set(folded) <= set(" abcdefghijklmnopqrstuvwxyz0123456789")
