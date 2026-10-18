"""Accent-insensitive folding and word matching for food search text.

The same character table is compiled into the ``food_reference.name_search``
generated column, so a folded query and a folded catalog row agree character
for character ("Phở bò", "pho bo" and "PHO BO" all fold to `` pho bo``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    FOOD_NAME_QUALIFIERS,
)

MAX_FOOD_SEARCH_WORDS = 6

# Food names fit well inside this; longer input is pasted text.
MAX_FOOD_SEARCH_QUERY_LENGTH = 100


# Letters whose canonical decomposition does not expose an ASCII base.
_EXPLICIT_BASES = {
    "Đ": "d",
    "đ": "d",
    "Ø": "o",
    "ø": "o",
    "Ł": "l",
    "ł": "l",
    "ı": "i",
}

# Latin-1 Supplement, Latin Extended-A/B and Latin Extended Additional, which
# holds every precomposed Vietnamese vowel with tone mark.
_ACCENTED_LATIN_RANGES = (range(0x00C0, 0x0250), range(0x1E00, 0x1F00))


def _build_fold_table() -> tuple[str, str, str]:
    sources: list[str] = []
    targets: list[str] = []
    for code_point in range(ord("A"), ord("Z") + 1):
        sources.append(chr(code_point))
        targets.append(chr(code_point).lower())
    for code_range in _ACCENTED_LATIN_RANGES:
        for code_point in code_range:
            char = chr(code_point)
            base = _EXPLICIT_BASES.get(char)
            if base is None:
                decomposed = unicodedata.normalize("NFD", char)
                head = decomposed[0]
                if len(decomposed) < 2 or not (head.isascii() and head.isalpha()):
                    continue
                base = head.lower()
            sources.append(char)
            targets.append(base)
    # Combining marks arrive from keyboards that emit decomposed (NFD) text:
    # base letter followed by tone/diacritic marks. They are dropped.
    deleted = "".join(chr(code_point) for code_point in range(0x0300, 0x0370))
    return "".join(sources), "".join(targets), deleted


FOLD_SOURCE_CHARS, FOLD_TARGET_CHARS, FOLD_DELETED_CHARS = _build_fold_table()

_FOLD_TRANSLATION = str.maketrans(
    FOLD_SOURCE_CHARS, FOLD_TARGET_CHARS, FOLD_DELETED_CHARS
)
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def clip_food_search_query(query: str | None) -> str:
    """Return the start of ``query`` that search works on.

    Searching only the start of pasted text keeps the cache key, translation,
    provider call and SQL patterns bounded whatever the client sends.
    """
    return str(query or "")[:MAX_FOOD_SEARCH_QUERY_LENGTH]


def fold_food_search_text(text: str | None) -> str:
    """Fold text to lowercase ASCII words, each preceded by one space.

    The leading space lets ``LIKE '% word%'`` anchor on word starts, including
    the first word. Empty input folds to a single space.
    """
    translated = str(text or "").translate(_FOLD_TRANSLATION)
    return " " + _NON_ALPHANUMERIC.sub(" ", translated).strip(" ")


def food_search_words(text: str | None) -> list[str]:
    """Return the folded words of ``text`` in order."""
    return fold_food_search_text(text).split()


def required_food_search_words(query: str | None) -> list[str]:
    """Folded words every local result must contain, in query order.

    Preparation qualifiers ("grilled", "raw") are dropped while other words
    remain because catalog names rarely carry them; a query made only of
    qualifiers keeps them so it still matches something.
    """
    words = list(dict.fromkeys(food_search_words(query)))
    kept = [word for word in words if word not in FOOD_NAME_QUALIFIERS]
    return (kept or words)[:MAX_FOOD_SEARCH_WORDS]


def is_strong_food_search_match(words: Iterable[str], *names: str | None) -> bool:
    """Return True when every query word starts a word of one of ``names``.

    Weak matches only contain a query word inside a longer word ("raw" in
    "strawberry"); they are kept as results but do not count as coverage.
    """
    query_words = list(words)
    if not query_words:
        return False
    name_words = [word for name in names for word in food_search_words(name)]
    return all(
        any(candidate.startswith(word) for candidate in name_words)
        for word in query_words
    )
