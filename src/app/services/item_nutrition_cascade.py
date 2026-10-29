"""Shared food-item nutrition cascade.

Parse-text and meal scan both resolve an item as local food reference, then
FatSecret, then an estimate. Parse-text keeps its custom g/kg estimate.
Meal scan keeps the vision macros and labels that tier ``ai_estimate``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from src.app.services.food_reference_validation_service import (
    ValidationItemReport,
    ValidationReport,
)
from src.app.services.parse_text_custom_estimate import apply_custom_estimate
from src.domain.model.meal import Meal
from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros_ops import merge_micros
from src.domain.services.nutrition_calculation_service import (
    MASS_VOLUME_CANONICAL_UNITS,
    UNIT_TO_GRAMS,
    _convert_with_allowed_units,
    _normalize_unit,
    canonicalize_mass_volume_unit,
    convert_quantity_to_grams,
    normalize_unit_for_manual_save,
)
from src.domain.services.nutrition_calculation_service import (
    scale_per_100g_nutrition as default_scale_per_100g_nutrition,
)
from src.domain.services.nutrition_integrity_policy import (
    NUTRITION_INTEGRITY_POLICY_VERSION,
    normalize_serving_options,
)
from src.domain.services.nutrition_resolver import (
    normalize_food_lookup_name,
    preparation_matches,
    select_nutrition_candidate,
    validate_ai_fallback,
    validate_reference_candidate,
)

logger = logging.getLogger(__name__)

MAX_PROVIDER_SEARCHES = 5
MAX_PROVIDER_DETAILS = 5
MAX_PROVIDER_CONCURRENCY = 3
TRUSTED_QUANTITY_UNITS = {
    "g",
    "gram",
    "grams",
    "kg",
    "kilogram",
    "kilograms",
    "ml",
    "l",
    "liter",
    "litre",
    "oz",
    "ounce",
    "ounces",
    "lb",
    "pound",
    "pounds",
}
COUNTABLE_GRAM_UNITS = set(UNIT_TO_GRAMS) - {"kg", "lb", "oz"}
_LOCAL_REFERENCE_SOURCES = frozenset(
    {
        "usda",
        "usda_fdc",
        "fooddata_central",
        "fatsecret",
        "food_reference",
        "seed",
        "catalog_seed",
        "nin",
    }
)
FoodReferenceBatchLookup = Callable[[list[str]], Awaitable[dict[str, dict[str, Any]]]]
ParseFatSecretNutrition = Callable[[dict[str, Any]], dict[str, Any] | None]
ScalePer100gNutrition = Callable[..., dict[str, Any]]
UnresolvedFallback = Callable[[dict[str, Any]], dict[str, Any] | None]


@dataclass
class ParseTextRequestBudget:
    """Request-wide limits shared by AI and staged reference resolution."""

    deadline: float | None = None
    ai_generations: int = 0
    provider_searches: int = 0
    provider_details: int = 0
    ai_seconds: float = 0.0
    reference_seconds: float = 0.0
    cache_hit: bool = False
    request_deadline: float | None = None
    semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_PROVIDER_CONCURRENCY)
    )


def preferred_parse_unit(item: dict[str, Any]) -> str:
    """Prefer the local unit when it is a real mass, volume, or countable unit."""
    local = str(item.get("unit") or "").strip()
    english = str(item.get("english_unit") or "").strip()
    if local:
        canonical = canonicalize_mass_volume_unit(local)
        if canonical in MASS_VOLUME_CANONICAL_UNITS:
            return canonical
        local_norm = _normalize_unit(local)
        if local_norm in TRUSTED_QUANTITY_UNITS or local_norm in COUNTABLE_GRAM_UNITS:
            return local
    return english or local or "serving"


def derive_calories_from_macros(macros: dict[str, Any]) -> float:
    protein = float(macros.get("protein_g", 0.0) or 0.0)
    carbs = float(macros.get("carbs_g", 0.0) or 0.0)
    fiber = float(macros.get("fiber_g", 0.0) or 0.0)
    fat = float(macros.get("fat_g", 0.0) or 0.0)
    return round(Macros.raw_total_calories(protein, carbs, fat, fiber), 2)


def extract_english_name(name: str) -> str:
    """Extract English name for FatSecret lookup.

    AI may return either format:
    - 'English Name (Local Name)' → extract before parens
    - 'Local Name (English Name)' → extract inside parens
    Heuristic: if text inside parens is ASCII, it's English; otherwise
    the text before parens is English.
    """
    match = re.search(r"^(.+?)\s*\(([^)]+)\)$", name.strip())
    if not match:
        return name
    before, inside = match.group(1), match.group(2)
    if all(ord(c) < 256 for c in inside.replace(" ", "")):
        return inside
    return before


def quantity_in_grams(item: dict[str, Any], food_name: str) -> float:
    unit = preferred_parse_unit(item)
    normalized_unit = normalize_unit_for_manual_save(unit)
    if normalized_unit in {"ml", "l", "cup", "tbsp", "tsp", "floz"}:
        quantity = float(item.get("quantity") or 1)
        return convert_quantity_to_grams(quantity, normalized_unit, food_name)
    if item.get("quantity_g") is not None:
        return float(item["quantity_g"])
    quantity = float(item.get("quantity") or 1)
    return convert_quantity_to_grams(quantity, normalized_unit, food_name)


def local_reference_is_usable(
    reference: dict[str, Any], lookup_name: str, preparation: str
) -> bool:
    if not reference.get("is_verified"):
        return False
    source = str(reference.get("source") or "").lower()
    if source not in _LOCAL_REFERENCE_SOURCES:
        return False
    return preparation_matches(str(reference.get("name") or lookup_name), preparation)


def safe_allowed_units(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    units: list[dict[str, Any]] = []
    for entry in raw[:12]:
        if not isinstance(entry, dict):
            continue
        unit = str(entry.get("unit") or entry.get("name") or "").strip()
        description = str(entry.get("description") or "").strip()
        raw_gram_weight = entry.get("gram_weight") or entry.get("grams")
        if raw_gram_weight is None:
            continue
        try:
            gram_weight = float(raw_gram_weight)
        except (TypeError, ValueError):
            continue
        if (
            not unit
            or len(unit) > 100
            or len(description) > 100
            or not gram_weight > 0
            or not all(ord(char) >= 32 for char in unit + description)
        ):
            continue
        units.append(
            {
                "unit": unit,
                "gram_weight": gram_weight,
                "description": description,
            }
        )
    return normalize_serving_options(units, provider_100g_label=True) or []


def reference_identity(raw: dict[str, Any], source: str) -> dict[str, Any]:
    raw_source = str(raw.get("source") or source or "").lower()
    if (
        source == "local_reference"
        or raw_source == "food_reference"
        or raw.get("food_reference_id") is not None
    ):
        reference_id = raw.get("food_reference_id") or raw.get("id")
        return {
            "origin": "local",
            "food_reference_id": reference_id,
            "food_id": f"food_reference:{reference_id}",
            "source_namespace": "food_reference",
            "source_food_id": str(reference_id),
        }
    if raw.get("fdc_id") is not None or raw.get("fdcId") is not None:
        fdc_id = raw.get("fdc_id") or raw.get("fdcId")
        return {
            "origin": "usda",
            "food_id": f"usda_fdc:{fdc_id}",
            "source_namespace": "usda_fdc",
            "source_food_id": str(fdc_id),
        }
    namespace = str(raw.get("source_namespace") or raw_source or "fatsecret")
    source_id = str(raw.get("source_food_id") or raw.get("food_id") or "")
    if source_id.startswith(f"{namespace}:"):
        source_id = source_id[len(namespace) + 1 :]
    if not source_id:
        return {
            "origin": "provider",
            "food_id": None,
            "source_namespace": namespace,
            "source_food_id": None,
        }
    return {
        "origin": "provider",
        "food_id": f"{namespace}:{source_id}",
        "source_namespace": namespace,
        "source_food_id": source_id,
    }


def canonicalize_reference_quantity(
    item: dict[str, Any],
    *,
    quantity_g: float,
    allowed_units: list[dict[str, Any]],
) -> float:
    """Keep parsed portions saveable against the same source snapshot."""
    quantity = float(item.get("quantity") or 1.0)
    source_unit = canonicalize_mass_volume_unit(
        str(item.get("english_unit") or item.get("unit") or "g")
    )
    try:
        authoritative_grams = _convert_with_allowed_units(
            quantity,
            source_unit,
            allowed_units,
            str(item.get("lookup_name") or item.get("name") or ""),
            strict=True,
        )
    except ValueError:
        item["quantity"] = quantity_g
        item["unit"] = "g"
        item["english_unit"] = "g"
        item["quantity_g"] = quantity_g
        return quantity_g

    item["unit"] = source_unit
    item["english_unit"] = source_unit
    item["quantity_g"] = authoritative_grams
    return authoritative_grams


def apply_reference(
    item: dict[str, Any],
    candidate: Any,
    quantity_g: float,
    source: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    allowed_units = safe_allowed_units(
        raw.get("allowed_units") or raw.get("serving_sizes")
    )
    if not allowed_units:
        allowed_units = list(candidate.allowed_units or [])
    quantity_g = canonicalize_reference_quantity(
        item,
        quantity_g=quantity_g,
        allowed_units=allowed_units,
    )
    factor = quantity_g / 100.0
    item.update(
        {
            "protein": round(candidate.protein_per_100g * factor, 2),
            "carbs": round(candidate.carbs_per_100g * factor, 2),
            "fat": round(candidate.fat_per_100g * factor, 2),
            "fiber": round(candidate.fiber_per_100g * factor, 2),
            "sugar": round(candidate.sugar_per_100g * factor, 2),
        }
    )
    item["calories"] = derive_calories_from_macros(item)
    item["data_source"] = (
        "fatsecret"
        if source == "fatsecret" or str(raw.get("source", "")).lower() == "fatsecret"
        else "usda"
    )
    item["fdc_id"] = raw.get("fdc_id")
    item.update(reference_identity(raw, source))
    catalog_name = str(
        raw.get("food_name")
        or raw.get("description")
        or raw.get("name")
        or item.get("lookup_name")
        or ""
    ).strip()
    if catalog_name:
        item["canonical_name"] = catalog_name
    item["nutrition_basis"] = "100g"
    item["nutrition_contract_version"] = NUTRITION_INTEGRITY_POLICY_VERSION
    item["calories_per_100g"] = derive_calories_from_macros(
        {
            "protein_g": candidate.protein_per_100g,
            "carbs_g": candidate.carbs_per_100g,
            "fat_g": candidate.fat_per_100g,
            "fiber_g": candidate.fiber_per_100g,
        }
    )
    item["protein_per_100g"] = candidate.protein_per_100g
    item["carbs_per_100g"] = candidate.carbs_per_100g
    item["fat_per_100g"] = candidate.fat_per_100g
    item["fiber_per_100g"] = candidate.fiber_per_100g
    item["sugar_per_100g"] = candidate.sugar_per_100g
    item["allowed_units"] = allowed_units
    extras = raw.get("extra_nutrients")
    if extras:
        item["extra_nutrients"] = extras
    return item


async def provider_call(
    awaitable_factory: Callable[[], Awaitable[Any]],
    budget: ParseTextRequestBudget,
) -> Any:
    deadline = budget.deadline
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    async with budget.semaphore:
        deadline = budget.deadline
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            return await asyncio.wait_for(awaitable_factory(), timeout=remaining)
        except Exception as exc:
            logger.debug("parse_text provider call failed: %s", type(exc).__name__)
            return None


async def resolve_legacy_provider(
    item: dict[str, Any],
    lookup_name: str,
    _quantity: float,
    _unit: str,
    budget: ParseTextRequestBudget,
    provider: Any,
    *,
    parse_fatsecret_nutrition: ParseFatSecretNutrition | None = None,
    scale_per_100g_nutrition: ScalePer100gNutrition | None = None,
) -> dict[str, Any] | None:
    if provider is None:
        return None

    def parse_nutrition(selected: dict[str, Any]) -> dict[str, Any] | None:
        if parse_fatsecret_nutrition is not None:
            return parse_fatsecret_nutrition(selected)
        from src.app.handlers.command_handlers.meal_text_parsing_utils import (
            parse_fatsecret_nutrition as imported_parse,
        )

        parsed = imported_parse(selected)
        return None if parsed is None else dict(parsed)

    scale_nutrition = scale_per_100g_nutrition or default_scale_per_100g_nutrition
    if budget.provider_searches >= MAX_PROVIDER_SEARCHES:
        return None
    budget.provider_searches += 1
    try:
        results = await provider_call(
            lambda: provider.search_foods(lookup_name, max_results=5),
            budget,
        )
    except Exception as exc:
        logger.debug("parse_text legacy provider failed: %s", type(exc).__name__)
        return None
    if not results:
        return None
    selected = select_nutrition_candidate(results, lookup_name)
    if selected is None:
        return None
    if not reference_identity(selected, "fatsecret")["source_food_id"]:
        return None
    structured = validate_reference_candidate(selected, require_energy=False)
    per_100g = (
        {
            "calories_100g": selected.get("calories_100g"),
            "protein_100g": selected.get("protein_100g"),
            "carbs_100g": selected.get("carbs_100g"),
            "fat_100g": selected.get("fat_100g"),
            "fiber_100g": selected.get("fiber_100g", 0),
            "sugar_100g": selected.get("sugar_100g", 0),
        }
        if structured
        else parse_nutrition(selected)
    )
    if not per_100g:
        return None
    allowed_units = safe_allowed_units(selected.get("allowed_units"))
    quantity_g = canonicalize_reference_quantity(
        item,
        quantity_g=quantity_in_grams(item, lookup_name),
        allowed_units=allowed_units,
    )
    scaled = scale_nutrition(
        {
            "calories": per_100g.get("calories_100g", per_100g.get("calories", 0)),
            "protein": per_100g.get("protein_100g", per_100g.get("protein", 0)),
            "carbs": per_100g.get("carbs_100g", per_100g.get("carbs", 0)),
            "fat": per_100g.get("fat_100g", per_100g.get("fat", 0)),
            "fiber": per_100g.get("fiber_100g", per_100g.get("fiber", 0)),
            "sugar": per_100g.get("sugar_100g", per_100g.get("sugar", 0)),
        },
        item["quantity"],
        item["unit"],
        allowed_units=allowed_units,
        food_name=lookup_name,
        strict_allowed_units=True,
    )
    if not validate_ai_fallback(
        name=lookup_name,
        protein=scaled.get("protein"),
        carbs=scaled.get("carbs"),
        fat=scaled.get("fat"),
        fiber=scaled.get("fiber"),
        quantity_g=quantity_g,
    ):
        return None
    item.update(scaled)
    item["calories"] = derive_calories_from_macros(item)
    item["allowed_units"] = allowed_units
    item["data_source"] = "fatsecret"
    extras = selected.get("extra_nutrients")
    if extras:
        item["extra_nutrients"] = extras
    item.update(reference_identity(selected, "fatsecret"))
    catalog_name = str(
        selected.get("food_name")
        or selected.get("description")
        or selected.get("name")
        or ""
    ).strip()
    if catalog_name:
        item["canonical_name"] = catalog_name
    item["nutrition_basis"] = "100g"
    item["nutrition_contract_version"] = NUTRITION_INTEGRITY_POLICY_VERSION
    item["calories_per_100g"] = derive_calories_from_macros(
        {
            "protein_g": per_100g.get("protein_100g", per_100g.get("protein", 0)),
            "carbs_g": per_100g.get("carbs_100g", per_100g.get("carbs", 0)),
            "fat_g": per_100g.get("fat_100g", per_100g.get("fat", 0)),
            "fiber_g": per_100g.get("fiber_100g", per_100g.get("fiber", 0)),
        }
    )
    item["protein_per_100g"] = per_100g.get("protein_100g", per_100g.get("protein", 0))
    item["carbs_per_100g"] = per_100g.get("carbs_100g", per_100g.get("carbs", 0))
    item["fat_per_100g"] = per_100g.get("fat_100g", per_100g.get("fat", 0))
    item["fiber_per_100g"] = per_100g.get("fiber_100g", per_100g.get("fiber", 0))
    item["sugar_per_100g"] = per_100g.get("sugar_100g", per_100g.get("sugar", 0))
    return item


async def resolve_staged_provider(
    item: dict[str, Any],
    lookup_name: str,
    preparation: str,
    quantity_g: float,
    budget: ParseTextRequestBudget,
    provider: Any,
    *,
    parse_fatsecret_nutrition: ParseFatSecretNutrition | None = None,
    scale_per_100g_nutrition: ScalePer100gNutrition | None = None,
) -> dict[str, Any] | None:
    if provider is None:
        return None
    if not hasattr(provider, "search_food_candidates"):
        return await resolve_legacy_provider(
            item,
            lookup_name,
            float(item.get("quantity") or 1),
            item.get("english_unit") or item.get("unit", "serving"),
            budget,
            provider,
            parse_fatsecret_nutrition=parse_fatsecret_nutrition,
            scale_per_100g_nutrition=scale_per_100g_nutrition,
        )
    if budget.provider_searches >= MAX_PROVIDER_SEARCHES:
        return None
    budget.provider_searches += 1
    candidates = await provider_call(
        lambda: provider.search_food_candidates(lookup_name, max_results=5),
        budget,
    )
    if candidates is None:
        return None
    selected = select_nutrition_candidate(candidates, lookup_name, preparation)
    if not selected or not selected.get("food_id"):
        return None
    if budget.provider_details >= MAX_PROVIDER_DETAILS:
        return None
    budget.provider_details += 1
    details = await provider_call(
        lambda: provider.get_food_details(str(selected["food_id"])),
        budget,
    )
    if not isinstance(details, dict):
        return None
    merged = {**selected, **details}
    candidate = validate_reference_candidate(merged)
    if candidate is None:
        return None
    return apply_reference(item, candidate, quantity_g, "fatsecret", merged)


async def cascade_lookup(
    item: dict[str, Any],
    *,
    budget: ParseTextRequestBudget,
    local_reference: dict[str, Any] | None = None,
    provider: Any = None,
    structured_reference_enabled: bool = True,
    on_unresolved: UnresolvedFallback | None = None,
    parse_fatsecret_nutrition: ParseFatSecretNutrition | None = None,
    scale_per_100g_nutrition: ScalePer100gNutrition | None = None,
) -> dict[str, Any] | None:
    """Resolve local catalog, then FatSecret; otherwise keep the fallback estimate."""
    name = str(item.get("name") or "")
    lookup_name = str(item.get("lookup_name") or extract_english_name(name))
    preparation = str(item.get("preparation") or "unknown")
    quantity = float(item.get("quantity") or 1.0)
    unit = item.get("english_unit") or item.get("unit", "serving")
    quantity_g = quantity_in_grams(item, lookup_name)

    if local_reference and local_reference_is_usable(
        local_reference, lookup_name, preparation
    ):
        candidate = validate_reference_candidate(
            local_reference,
            require_energy=False,
            require_metric_basis=False,
        )
        if candidate:
            return apply_reference(
                item, candidate, quantity_g, "local_reference", local_reference
            )

    if provider and structured_reference_enabled:
        structured = await resolve_staged_provider(
            item,
            lookup_name,
            preparation,
            quantity_g,
            budget,
            provider,
            parse_fatsecret_nutrition=parse_fatsecret_nutrition,
            scale_per_100g_nutrition=scale_per_100g_nutrition,
        )
        if structured is not None:
            return structured
    elif provider:
        legacy = await resolve_legacy_provider(
            item,
            lookup_name,
            quantity,
            unit,
            budget,
            provider,
            parse_fatsecret_nutrition=parse_fatsecret_nutrition,
            scale_per_100g_nutrition=scale_per_100g_nutrition,
        )
        if legacy is not None:
            return legacy

    fallback = on_unresolved or apply_custom_estimate
    return fallback(item)


def mark_ai_estimate(item: dict[str, Any]) -> dict[str, Any]:
    """Keep the vision macros and name the unresolved tier."""
    item["data_source"] = "ai_estimate"
    item["origin"] = "ai_estimate"
    return item


def _scan_report_outcome(
    items: list[ValidationItemReport],
) -> Literal["match", "diverge", "unresolved"]:
    outcomes = {item.outcome for item in items}
    if "diverge" in outcomes:
        return "diverge"
    if "match" in outcomes:
        return "match"
    return "unresolved"


def _ai_estimate_report(item_id: str, reason_code: str) -> ValidationItemReport:
    return ValidationItemReport(
        item_id=item_id,
        outcome="unresolved",
        source=None,
        reason_code=reason_code,
    )


def _reference_report(item_id: str, resolved: dict[str, Any]) -> ValidationItemReport:
    origin = str(resolved.get("origin") or "")
    if origin == "local":
        return ValidationItemReport(
            item_id=item_id,
            outcome="match",
            source="local",
            reason_code="food_reference",
        )
    data_source = str(resolved.get("data_source") or "")
    if data_source == "usda" or origin == "usda":
        source = "usda"
    elif data_source == "fatsecret":
        source = "fatsecret"
    else:
        source = "provider"
    return ValidationItemReport(
        item_id=item_id,
        outcome="match",
        source=source,
        reason_code=source,
    )


def _snapshot_from_resolved(resolved: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "basis": resolved.get("nutrition_basis") or "100g",
        "protein_per_100g": resolved.get("protein_per_100g"),
        "carbs_per_100g": resolved.get("carbs_per_100g"),
        "fat_per_100g": resolved.get("fat_per_100g"),
        "fiber_per_100g": resolved.get("fiber_per_100g"),
        "sugar_per_100g": resolved.get("sugar_per_100g"),
        "calories_per_100g": resolved.get("calories_per_100g"),
        "allowed_units": list(resolved.get("allowed_units") or []),
        "origin": resolved.get("origin"),
        "source_namespace": resolved.get("source_namespace"),
        "source_food_id": resolved.get("source_food_id"),
        "data_source": resolved.get("data_source"),
    }
    extras = resolved.get("extra_nutrients")
    if isinstance(extras, dict) and extras:
        snapshot["extra_nutrients"] = extras
    canonical_name = resolved.get("canonical_name")
    if isinstance(canonical_name, str) and canonical_name.strip():
        snapshot["canonical_name"] = canonical_name.strip()
    return snapshot


def _apply_resolved_scan_item(item: Any, resolved: dict[str, Any] | None) -> str:
    """Write a cascade result onto a scan food item. Returns the tier name."""
    if not isinstance(resolved, dict):
        item.source_kind = "ai_estimate"
        return "ai_estimate"
    origin = str(resolved.get("origin") or "")
    data_source = str(resolved.get("data_source") or "")
    if origin == "ai_estimate" or data_source == "ai_estimate":
        item.source_kind = "ai_estimate"
        return "ai_estimate"
    if origin not in {"local", "provider", "usda"} and data_source not in {
        "fatsecret",
        "usda",
    }:
        item.source_kind = "ai_estimate"
        return "ai_estimate"

    item.macros = Macros(
        protein=float(resolved.get("protein") or 0.0),
        carbs=float(resolved.get("carbs") or 0.0),
        fat=float(resolved.get("fat") or 0.0),
        fiber=float(resolved.get("fiber") or 0.0),
        sugar=float(resolved.get("sugar") or 0.0),
    )
    if resolved.get("quantity") is not None:
        item.quantity = float(resolved["quantity"])
    unit = resolved.get("unit")
    if isinstance(unit, str) and unit.strip():
        item.unit = unit
    reference_id = resolved.get("food_reference_id")
    if reference_id is not None:
        try:
            item.food_reference_id = int(reference_id)
        except (TypeError, ValueError):
            item.food_reference_id = None
    fdc_id = resolved.get("fdc_id")
    if fdc_id is not None:
        try:
            item.fdc_id = int(fdc_id)
        except (TypeError, ValueError):
            item.fdc_id = None
    item.source_kind = origin if origin in {"local", "provider", "usda"} else "provider"
    source_food_id = resolved.get("source_food_id")
    item.source_food_id = (
        None if source_food_id is None else str(source_food_id)
    ) or None
    contract = resolved.get("nutrition_contract_version")
    item.nutrition_contract_version = contract if isinstance(contract, str) else None
    allowed_units = resolved.get("allowed_units")
    if isinstance(allowed_units, list) and allowed_units:
        item.allowed_units = list(allowed_units)
    item.source_snapshot = _snapshot_from_resolved(resolved)
    extras = resolved.get("extra_nutrients")
    quantity_g = resolved.get("quantity_g")
    if isinstance(extras, dict) and extras and quantity_g is not None:
        try:
            factor = float(quantity_g) / 100.0
        except (TypeError, ValueError):
            factor = 0.0
        micros = (
            extra_nutrients_to_micros(extras, factor=factor) if factor > 0 else None
        )
        if micros is not None:
            item.micros = micros
    return "food_reference" if origin == "local" else "fatsecret"


def _scan_item_dict(item: Any) -> dict[str, Any]:
    return {
        "name": item.name,
        "lookup_name": item.name,
        "preparation": "unknown",
        "quantity": float(item.quantity),
        "unit": item.unit,
        "english_unit": item.unit,
        "quantity_g": float(item.quantity),
        "protein": float(item.macros.protein),
        "carbs": float(item.macros.carbs),
        "fat": float(item.macros.fat),
        "fiber": float(item.macros.fiber),
        "sugar": float(item.macros.sugar),
    }


def _retotal_meal_nutrition(meal: Meal) -> None:
    nutrition = meal.nutrition
    if nutrition is None or not nutrition.food_items:
        return
    items = nutrition.food_items
    nutrition.macros = Macros(
        protein=sum(item.macros.protein for item in items),
        carbs=sum(item.macros.carbs for item in items),
        fat=sum(item.macros.fat for item in items),
        fiber=sum(item.macros.fiber for item in items),
        sugar=sum(item.macros.sugar for item in items),
    )
    nutrition.micros = merge_micros(*(item.micros for item in items))


async def ground_scan_meal(
    meal: Meal,
    *,
    batch_lookup: FoodReferenceBatchLookup | None,
    provider: Any,
    timeout_seconds: float,
) -> ValidationReport:
    """Replace scan item macros from food reference, then FatSecret, else AI."""
    if meal.source == "food_label":
        return ValidationReport(meal=meal, outcome="skipped", reason_code="food_label")
    nutrition = meal.nutrition
    if nutrition is None:
        return ValidationReport(
            meal=meal, outcome="skipped", reason_code="missing_nutrition"
        )
    if not nutrition.food_items:
        return ValidationReport(
            meal=meal, outcome="skipped", reason_code="no_food_items"
        )

    reports: list[ValidationItemReport] = []
    macros_changed = False
    deadline = time.monotonic() + timeout_seconds
    budget = ParseTextRequestBudget(deadline=deadline)

    async def _run() -> None:
        nonlocal macros_changed
        food_items = nutrition.food_items or []
        keys_by_item: dict[str, str] = {}
        lookup_keys: list[str] = []
        for food_item in food_items:
            key = normalize_food_lookup_name(str(food_item.name or ""))
            if not key:
                continue
            keys_by_item[food_item.id] = key
            lookup_keys.append(key)

        local_hits: dict[str, dict[str, Any]] = {}
        if batch_lookup is not None and lookup_keys and time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                local_hits = await asyncio.wait_for(
                    batch_lookup(sorted(set(lookup_keys))),
                    timeout=remaining,
                )
            except TimeoutError:
                raise
            except Exception as exc:
                logger.debug(
                    "scan reference lookup failed: %s",
                    type(exc).__name__,
                )
                local_hits = {}
        if not isinstance(local_hits, dict):
            local_hits = {}

        for food_item in food_items:
            if time.monotonic() >= deadline:
                raise TimeoutError
            if not str(food_item.name or "").strip():
                food_item.source_kind = "ai_estimate"
                reports.append(_ai_estimate_report(food_item.id, "empty_name"))
                continue
            before = (
                food_item.macros.protein,
                food_item.macros.carbs,
                food_item.macros.fat,
                food_item.macros.fiber,
                food_item.macros.sugar,
            )
            try:
                resolved = await cascade_lookup(
                    _scan_item_dict(food_item),
                    budget=budget,
                    local_reference=local_hits.get(keys_by_item.get(food_item.id, "")),
                    provider=provider,
                    structured_reference_enabled=True,
                    on_unresolved=mark_ai_estimate,
                )
            except Exception as exc:
                logger.debug("scan cascade failed: %s", type(exc).__name__)
                resolved = None
            tier = _apply_resolved_scan_item(food_item, resolved)
            after = (
                food_item.macros.protein,
                food_item.macros.carbs,
                food_item.macros.fat,
                food_item.macros.fiber,
                food_item.macros.sugar,
            )
            if before != after:
                macros_changed = True
            if tier == "ai_estimate":
                reports.append(_ai_estimate_report(food_item.id, "ai_estimate"))
            elif isinstance(resolved, dict):
                reports.append(_reference_report(food_item.id, resolved))
            else:
                reports.append(_ai_estimate_report(food_item.id, "ai_estimate"))
        if macros_changed:
            _retotal_meal_nutrition(meal)

    try:
        await asyncio.wait_for(_run(), timeout=timeout_seconds)
    except TimeoutError:
        seen = {report.item_id for report in reports}
        for food_item in nutrition.food_items:
            if food_item.id in seen:
                continue
            food_item.source_kind = "ai_estimate"
            reports.append(_ai_estimate_report(food_item.id, "ai_estimate"))
        if macros_changed:
            _retotal_meal_nutrition(meal)
        return ValidationReport(
            meal=meal,
            outcome="timeout",
            reason_code="timeout",
            error_type="TimeoutError",
            items=tuple(reports),
        )
    except Exception as exc:
        if macros_changed:
            _retotal_meal_nutrition(meal)
        return ValidationReport(
            meal=meal,
            outcome="error",
            reason_code="provider_error",
            error_type=type(exc).__name__,
            items=tuple(reports),
        )
    return ValidationReport(
        meal=meal,
        outcome=_scan_report_outcome(reports),
        items=tuple(reports),
    )
