"""Query length, words and match strength used by local food search."""

import pytest

from src.domain.utils.food_search_text import (
    MAX_FOOD_SEARCH_QUERY_LENGTH,
    MAX_FOOD_SEARCH_WORDS,
    clip_food_search_query,
    is_strong_food_search_match,
    required_food_search_words,
)


@pytest.mark.parametrize(
    ("query", "searched"),
    [
        ("  phở bò  ", "  phở bò  "),
        ("a" * MAX_FOOD_SEARCH_QUERY_LENGTH, "a" * MAX_FOOD_SEARCH_QUERY_LENGTH),
        ("", ""),
        (None, ""),
    ],
)
def test_queries_within_the_length_limit_are_searched_whole(
    query: str | None, searched: str
) -> None:
    assert clip_food_search_query(query) == searched


def test_longer_queries_are_searched_by_their_start() -> None:
    pasted = "phở bò tái " * 50

    searched = clip_food_search_query(pasted)

    assert searched == pasted[:MAX_FOOD_SEARCH_QUERY_LENGTH]
    assert len(searched) == MAX_FOOD_SEARCH_QUERY_LENGTH


@pytest.mark.parametrize(
    ("query", "words"),
    [
        ("Phở bò", ["pho", "bo"]),
        ("pho  BO", ["pho", "bo"]),
        ("grilled chicken breast", ["chicken", "breast"]),
        ("Raw", ["raw"]),
        ("grilled boneless", ["grilled", "boneless"]),
        ("rice rice RICE", ["rice"]),
        ("", []),
        (None, []),
        ("!!!", []),
    ],
)
def test_required_words_fold_dedupe_and_drop_qualifiers(
    query: str | None, words: list[str]
) -> None:
    assert required_food_search_words(query) == words


def test_required_words_are_capped() -> None:
    query = "a b c d e f g h"

    assert required_food_search_words(query) == list("abcdef")
    assert len(required_food_search_words(query)) == MAX_FOOD_SEARCH_WORDS


@pytest.mark.parametrize(
    ("words", "names"),
    [
        (["pho", "bo"], ("Phở bò", None)),
        (["pho", "bo"], (None, "Pho Bo, beef noodle soup")),
        (["chick"], ("Chicken breast",)),
        (["bun", "hue"], ("Bún bò Huế", "Hue beef noodle soup")),
        (["ga"], ("Cơm gà", "Chicken rice")),
    ],
)
def test_strong_match_needs_every_word_at_a_word_start(
    words: list[str], names: tuple[str | None, ...]
) -> None:
    assert is_strong_food_search_match(words, *names) is True


@pytest.mark.parametrize(
    ("words", "names"),
    [
        (["raw"], ("Strawberry",)),
        (["pho", "bo"], ("Phở gà", "Chicken pho")),
        (["bo"], ("Jumbo shrimp",)),
        ([], ("Rice",)),
        (["rice"], (None, "")),
    ],
)
def test_weak_or_missing_words_are_not_strong_matches(
    words: list[str], names: tuple[str | None, ...]
) -> None:
    assert is_strong_food_search_match(words, *names) is False
