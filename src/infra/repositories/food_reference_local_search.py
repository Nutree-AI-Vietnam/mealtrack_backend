"""SQL for accent-insensitive local food search over ``food_reference``.

Rows match on the generated ``name_search`` column (folded ``name_vi`` and
``name`` words), so "pho bo", "phở bò" and "PHO BO" find the same rows and every
predicate stays on its trigram index.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from src.domain.services.food_reference_identity import (
    FATSECRET_NAMESPACE,
    platform_identity_prefix,
)
from src.domain.utils.food_search_text import (
    clip_food_search_query,
    food_search_words,
    required_food_search_words,
)
from src.infra.database.models.food_reference_model import FoodReferenceModel

GLOBAL_REGION = "global"

# Shorter fragments only match at a word start: an infix "bo" would hit
# "jumbo" and "bolognese" for every "bò" query.
_INFIX_MIN_LENGTH = 3

# A whole-word phrase hit also accepts these English plural endings on its
# last word, so "egg" ranks "Eggs, scrambled" with "Egg, boiled".
_PLURAL_SUFFIXES = ("", "s", "es")


def search_regions(region: str) -> list[str]:
    """The requested region plus the shared global catalog."""
    return list(dict.fromkeys([region, GLOBAL_REGION]))


def build_local_search_statement(
    query: str | None,
    region: str,
    eligibility: ColumnElement[bool],
) -> Select[tuple[FoodReferenceModel]] | None:
    """Ranked statement for one search, or ``None`` when nothing can match.

    Quarantined rows stay hidden even while the integrity gate is pending.
    """
    raw_query = clip_food_search_query(
        unicodedata.normalize("NFC", str(query or ""))
    ).strip()
    identity_query = raw_query.lower()
    if identity_query.startswith(platform_identity_prefix(FATSECRET_NAMESPACE)):
        # Provider identity keys are looked up exactly, never substring-matched.
        match: ColumnElement[bool] = (
            func.lower(FoodReferenceModel.name_normalized) == identity_query
        )
        order: tuple[Any, ...] = (FoodReferenceModel.id.asc(),)
    else:
        words = required_food_search_words(raw_query)
        if not words:
            return None
        match = and_(*(_word_match(word) for word in words))
        order = _relevance_order(raw_query)
    return (
        select(FoodReferenceModel)
        .where(eligibility)
        .where(FoodReferenceModel.integrity_status != "quarantined")
        .where(FoodReferenceModel.region.in_(search_regions(region)))
        .where(match)
        .order_by(*order)
    )


def _word_match(word: str) -> ColumnElement[bool]:
    # Folded words are [a-z0-9]+, so they never carry LIKE wildcards.
    pattern = f"%{word}%" if len(word) >= _INFIX_MIN_LENGTH else f"% {word}%"
    return FoodReferenceModel.name_search.like(pattern)


def _relevance_order(raw_query: str) -> tuple[Any, ...]:
    """Exact accents, then whole-word phrase, word-start phrase, similarity."""
    phrase = " " + " ".join(food_search_words(raw_query))
    padded_name = FoodReferenceModel.name_search + " "
    phrase_rank = case(
        (
            or_(
                *(
                    padded_name.like(f"%{phrase}{suffix} %")
                    for suffix in _PLURAL_SUFFIXES
                )
            ),
            0,
        ),
        (FoodReferenceModel.name_search.like(f"%{phrase}%"), 1),
        else_=2,
    )
    similarity = func.greatest(
        func.similarity(FoodReferenceModel.name, phrase.strip()),
        func.similarity(FoodReferenceModel.name_search, phrase.strip()),
    )
    order: list[Any] = [phrase_rank, similarity.desc(), FoodReferenceModel.id.asc()]
    if not raw_query.isascii():
        # Folding makes "bò" (beef) and "bơ" (avocado) equal; a typed accent
        # still decides which comes first.
        display_name = (
            func.coalesce(FoodReferenceModel.name_vi, "")
            + " "
            + FoodReferenceModel.name
        )
        order.insert(
            0,
            case((display_name.icontains(raw_query, autoescape=True), 0), else_=1),
        )
    return tuple(order)
