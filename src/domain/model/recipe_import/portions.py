"""Amounts and eaten shares from the focused portion pass.

Reading a long source and estimating amounts in one AI call proved
unreliable: estimates were skipped or a whole batch of dipping sauce was
counted as eaten. A second, smaller call estimates every line's weight and
eaten share; this module checks its answer and merges it into the recipe.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.domain.model.recipe_import.extracted_recipe import ExtractedRecipe
from src.domain.model.recipe_import.limits import (
    MAX_INGREDIENT_GRAMS,
    MAX_INGREDIENT_QUANTITY,
    MAX_UNIT_CHARS,
    MIN_EATEN_SHARE,
)
from src.domain.model.recipe_import.text import clamp_servings, clean_text


@dataclass(frozen=True, slots=True)
class PortionLine:
    index: int
    grams: float | None
    quantity: float | None
    unit: str | None
    eaten_share: float


@dataclass(frozen=True, slots=True)
class PortionEstimate:
    servings: int | None
    lines: tuple[PortionLine, ...]


def sanitize_portion_estimate(
    raw: Mapping[str, Any], *, line_count: int
) -> PortionEstimate:
    lines: dict[int, PortionLine] = {}
    entries = raw.get("lines")
    if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)):
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            index = entry.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                continue
            if not 0 <= index < line_count or index in lines:
                continue
            lines[index] = PortionLine(
                index=index,
                grams=_positive(entry.get("grams"), MAX_INGREDIENT_GRAMS),
                quantity=_positive(entry.get("quantity"), MAX_INGREDIENT_QUANTITY),
                unit=clean_text(entry.get("unit"), max_chars=MAX_UNIT_CHARS),
                eaten_share=eaten_share(entry.get("eaten_share")),
            )
    servings = raw.get("servings")
    return PortionEstimate(
        servings=clamp_servings(servings)
        if isinstance(servings, int) and not isinstance(servings, bool)
        else None,
        lines=tuple(lines[index] for index in sorted(lines)),
    )


def apply_portion_estimate(
    recipe: ExtractedRecipe, estimate: PortionEstimate
) -> ExtractedRecipe:
    """Fill missing amounts and set eaten shares; stated amounts are kept."""
    by_index = {line.index: line for line in estimate.lines}
    items = []
    for index, item in enumerate(recipe.ingredients):
        line = by_index.get(index)
        if line is None:
            items.append(item)
            continue
        if item.stated_amount:
            items.append(
                replace(
                    item,
                    grams=item.grams if item.grams is not None else line.grams,
                    eaten_share=line.eaten_share,
                )
            )
            continue
        if line.grams is None:
            items.append(replace(item, eaten_share=line.eaten_share))
            continue
        # A count without its unit cannot be shown; show the weight instead.
        quantity, unit = (
            (line.quantity, line.unit)
            if line.quantity is not None and line.unit
            else (line.grams, "g")
        )
        items.append(
            replace(
                item,
                quantity=quantity,
                unit=unit,
                grams=line.grams,
                amount_estimated=True,
                eaten_share=line.eaten_share,
            )
        )
    servings = recipe.servings
    if not (recipe.servings_stated and recipe.servings) and estimate.servings:
        servings = estimate.servings
    return replace(recipe, ingredients=tuple(items), servings=servings)


def eaten_share(value: Any) -> float:
    """1 when unknown or not below 1; never below the minimum share."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    share = float(value)
    if not math.isfinite(share) or share >= 1:
        return 1.0
    return round(max(share, MIN_EATEN_SHARE), 3)


def _positive(value: Any, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > maximum:
        return None
    return round(number, 3)
