"""Resolve food identity and grams into deterministic nutrition."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from src.domain.model.nutrition import MAX_FOOD_ITEM_QUANTITY, Macros
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityPolicy


@dataclass(frozen=True)
class NutritionCandidate:
    """Per-100g structured nutrition data for a food candidate."""

    name: str
    protein_per_100g: float
    carbs_per_100g: float
    fat_per_100g: float
    fiber_per_100g: float = 0.0
    sugar_per_100g: float = 0.0
    source: str = "unknown"
    calories_per_100g: float | None = None
    preparation: str = "unknown"
    food_id: str | None = None
    brand: str | None = None
    food_type: str | None = None


@dataclass(frozen=True)
class ResolvedNutritionItem:
    """Resolved food item with scaled macros."""

    name: str
    grams: float
    macros: Macros
    source: str


PREPARATIONS = ("raw", "boiled", "baked", "fried", "mashed", "unknown")
PUBLIC_REFERENCE_SOURCES = {
    "usda": "usda",
    "usda_fdc": "usda",
    "fooddata_central": "usda",
    "fatsecret": "fatsecret",
}


def normalize_food_lookup_name(value: str) -> str:
    """Normalize names for exact, accent-insensitive reference lookup."""
    ascii_value = "".join(
        char
        for char in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def preparation_matches(name: str, preparation: str) -> bool:
    """Return whether a candidate name is compatible with requested preparation."""
    normalized = normalize_food_lookup_name(name)
    requested = preparation if preparation in PREPARATIONS else "unknown"
    if requested == "unknown":
        return not any(token in normalized.split() for token in PREPARATIONS[1:-1])
    aliases = {
        "raw": {"raw", "fresh", "uncooked"},
        "boiled": {"boiled", "cooked", "steamed", "poached"},
        "baked": {"baked", "roasted"},
        "fried": {"fried", "stir fried", "deep fried"},
        "mashed": {"mashed", "pureed"},
    }
    if requested == "raw":
        prepared_tokens = {
            "boiled",
            "cooked",
            "steamed",
            "poached",
            "baked",
            "roasted",
            "fried",
            "mashed",
            "pureed",
        }
        return any(
            normalize_food_lookup_name(alias) in normalized
            for alias in aliases[requested]
        ) or not prepared_tokens.intersection(normalized.split())
    return any(
        normalize_food_lookup_name(alias) in normalized for alias in aliases[requested]
    )


def score_nutrition_candidate(
    candidate: dict[str, Any],
    lookup_name: str,
    preparation: str = "unknown",
    *,
    brand_supplied: bool = False,
) -> float:
    """Score a provider candidate without trusting provider ordering."""
    candidate_name = str(
        candidate.get("food_name")
        or candidate.get("description")
        or candidate.get("name")
        or ""
    )
    target = normalize_food_lookup_name(lookup_name)
    normalized_candidate = normalize_food_lookup_name(candidate_name)
    if not target or not normalized_candidate:
        return float("-inf")
    if preparation != "unknown" and not preparation_matches(
        candidate_name, preparation
    ):
        return float("-inf")

    target_tokens = set(target.split())
    candidate_tokens = set(normalized_candidate.split())
    score = 10.0 if normalized_candidate == target else 0.0
    score += 2.0 * len(target_tokens & candidate_tokens) / max(len(target_tokens), 1)

    if preparation != "unknown":
        score += 4.0 if preparation_matches(candidate_name, preparation) else -4.0
    elif not preparation_matches(candidate_name, "unknown"):
        score -= 3.0

    food_type = str(candidate.get("food_type") or "").lower()
    if not brand_supplied and food_type == "generic":
        score += 2.0
    if not brand_supplied and (candidate.get("brand") or candidate.get("brand_name")):
        score -= 1.5

    risky_terms = {"concentrate", "dry", "mix", "supplement", "powder"}
    if risky_terms & candidate_tokens and not risky_terms & target_tokens:
        score -= 4.0
    return score


def select_nutrition_candidate(
    candidates: list[dict[str, Any]],
    lookup_name: str,
    preparation: str = "unknown",
    *,
    brand_supplied: bool = False,
) -> dict[str, Any] | None:
    """Select a confident candidate; reject low scores and ambiguous ties."""
    scored = [
        (
            score_nutrition_candidate(
                item, lookup_name, preparation, brand_supplied=brand_supplied
            ),
            item,
        )
        for item in candidates
    ]
    scored = [(score, item) for score, item in scored if math.isfinite(score)]
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    if best_score < 4.0 or (len(scored) > 1 and best_score == scored[1][0]):
        return None
    return best


def validate_reference_candidate(
    data: dict[str, Any],
    *,
    require_energy: bool = True,
    require_metric_basis: bool = True,
) -> NutritionCandidate | None:
    """Validate structured per-100g data before it can replace AI estimates."""
    source = str(data.get("source") or "fatsecret").lower()
    result = NutritionIntegrityPolicy().evaluate(
        data,
        require_energy=require_energy,
        require_metric_basis=require_metric_basis,
        provider_100g_label=source in {"fatsecret", "openfoodfacts", "provider"},
    )
    if not result.accepted:
        return None

    return NutritionCandidate(
        name=str(
            data.get("food_name") or data.get("description") or data.get("name") or ""
        ),
        protein_per_100g=result.protein_100g or 0.0,
        carbs_per_100g=result.carbs_100g or 0.0,
        fat_per_100g=result.fat_100g or 0.0,
        fiber_per_100g=result.fiber_100g or 0.0,
        sugar_per_100g=result.sugar_100g or 0.0,
        source=source,
        calories_per_100g=result.calories_100g,
        food_id=str(data["food_id"]) if data.get("food_id") is not None else None,
    )


def _optional_nonnegative(value: Any) -> float | None:
    if value is None:
        return 0.0
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) and converted >= 0 else None


def validate_ai_fallback(
    *,
    name: str,
    protein: Any,
    carbs: Any,
    fat: Any,
    fiber: Any = 0.0,
    quantity_g: float | None,
) -> bool:
    """Reject implausibly dense fallback macros when quantity is trustworthy."""
    if quantity_g is None or quantity_g <= 0:
        return True
    values = [_optional_nonnegative(value) for value in (protein, carbs, fat, fiber)]
    protein_value, carbs_value, fat_value, fiber_value = values
    if any(value is None for value in values):
        return False
    assert (
        protein_value is not None
        and carbs_value is not None
        and fat_value is not None
        and fiber_value is not None
    )
    if protein_value + carbs_value + fat_value > quantity_g * 1.1:
        return False
    calories = Macros.raw_total_calories(
        protein_value, carbs_value, fat_value, fiber_value
    )
    max_calories = quantity_g * (9.5 if _is_energy_dense_food(name) else 4.5)
    return calories <= max_calories + 20.0


def _is_energy_dense_food(name: str) -> bool:
    """Match oily/nut foods even when the token is compound (walnuts, almonds)."""
    normalized = normalize_food_lookup_name(name)
    tokens = set(normalized.split())
    dense_terms = {
        "oil",
        "butter",
        "lard",
        "ghee",
        "fat",
        "nut",
        "nuts",
        "seed",
        "seeds",
        "tahini",
        "concentrate",
        "powder",
        "flour",
    }
    if tokens & dense_terms:
        return True
    dense_names = (
        "walnut",
        "almond",
        "pecan",
        "cashew",
        "pistachio",
        "hazelnut",
        "macadamia",
        "peanut",
        "coconut",
        "avocado",
        "chocolate",
        "cocoa",
        "sesame",
        "sunflower",
        "chia",
        "flax",
        "mayo",
        "mayonnaise",
    )
    return any(term in normalized for term in dense_names)


class NutritionResolver:
    """Resolve recognized food names against structured nutrition data."""

    def __init__(self, local_candidates: dict[str, NutritionCandidate]) -> None:
        self._local_candidates = {
            key.strip().lower(): value for key, value in local_candidates.items()
        }

    async def resolve_item(
        self,
        *,
        name: str,
        estimated_grams: float,
    ) -> ResolvedNutritionItem:
        key = name.strip().lower()
        if not key:
            raise ValueError("food name must not be empty")
        if estimated_grams <= 0 or estimated_grams > MAX_FOOD_ITEM_QUANTITY:
            raise ValueError(
                "estimated_grams must be within supported food quantity bounds"
            )
        if key not in self._local_candidates:
            raise ValueError(f"No nutrition candidate found for food: {name}")

        candidate = self._local_candidates[key]
        factor = estimated_grams / 100.0
        return ResolvedNutritionItem(
            name=candidate.name,
            grams=estimated_grams,
            macros=Macros(
                protein=round(candidate.protein_per_100g * factor, 2),
                carbs=round(candidate.carbs_per_100g * factor, 2),
                fat=round(candidate.fat_per_100g * factor, 2),
                fiber=round(candidate.fiber_per_100g * factor, 2),
                sugar=round(candidate.sugar_per_100g * factor, 2),
            ),
            source=candidate.source,
        )
