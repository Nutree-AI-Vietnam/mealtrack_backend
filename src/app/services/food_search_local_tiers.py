"""Decide whether local catalog hits cover a search, and order them by strength."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Sequence
from typing import Any

from src.domain.utils.food_search_text import (
    is_strong_food_search_match,
    required_food_search_words,
)

# Five strong hits fill the first screen of the search sheet; a provider page
# would only add rows below the fold.
STRONG_MATCH_TARGET = 5

_NAME_FIELDS = ("name_vi", "name", "description")


def _query_word_sets(queries: Iterable[str | None]) -> list[list[str]]:
    word_sets = (required_food_search_words(query) for query in queries)
    return [words for words in word_sets if words]


def _is_strong(item: dict[str, Any], word_sets: Sequence[Sequence[str]]) -> bool:
    names = [item.get(field) for field in _NAME_FIELDS]
    return any(is_strong_food_search_match(words, *names) for words in word_sets)


def split_strong_matches(
    items: Sequence[dict[str, Any]], *queries: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split items into strong and weak matches, keeping their order.

    An item is strong when every word of any one query starts a word of its
    names; ``queries`` usually holds the typed text and its English form.
    """
    word_sets = _query_word_sets(queries)
    strong: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []
    for item in items:
        (strong if _is_strong(item, word_sets) else weak).append(item)
    return strong, weak


def count_strong_matches(items: Sequence[dict[str, Any]], *queries: str | None) -> int:
    return len(split_strong_matches(items, *queries)[0])


def local_results_are_sufficient(
    items: Sequence[dict[str, Any]], limit: int, *queries: str | None
) -> bool:
    """True when local rows alone answer the search.

    A full page always counts, as it did before strength was measured; a
    shorter page counts once it holds enough strong matches to fill the
    first screen.
    """
    if not items:
        return False
    if len(items) >= limit:
        return True
    return count_strong_matches(items, *queries) >= min(limit, STRONG_MATCH_TARGET)


def merge_local_tiers(
    native: Sequence[dict[str, Any]],
    translated: Sequence[dict[str, Any]],
    *,
    limit: int,
    key: Callable[[dict[str, Any]], Hashable],
    queries: Sequence[str | None],
) -> list[dict[str, Any]]:
    """Combine local hits for the typed and the translated query.

    Order: strong native, strong translated, weak native, weak translated —
    a Vietnamese row matching "cơm gà" stays ahead of an English row found
    through "chicken rice", and substring-only hits never displace either.
    """
    seen: set[Hashable] = set()
    unique: list[dict[str, Any]] = []
    for item in [*native, *translated]:
        item_key = key(item)
        if item_key in seen:
            continue
        seen.add(item_key)
        unique.append(item)
    strong, weak = split_strong_matches(unique, *queries)
    return [*strong, *weak][:limit]
