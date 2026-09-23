"""Resolve ingredient spellings onto canonical food_reference ids."""

from __future__ import annotations

from collections.abc import Iterable


def normalize_food_alias(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def resolve_food_alias(
    query: str, aliases: Iterable[tuple[str, int]]
) -> int | None:
    """Return the food id for a Vietnamese or English alias, if one exists."""

    key = normalize_food_alias(query)
    if not key:
        return None
    for alias, food_reference_id in aliases:
        if normalize_food_alias(alias) == key:
            return int(food_reference_id)
    return None
