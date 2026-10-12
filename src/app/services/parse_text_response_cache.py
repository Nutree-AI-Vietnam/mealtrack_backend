"""Versioned, bounded cache operations for complete parse-text responses."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from dataclasses import asdict
from typing import Any

from src.app.schemas.meal_schemas import ParsedFoodItemDto, ParseMealTextResponseDto
from src.domain.model.ai.nutrition_contracts import MealTextNutritionResponse
from src.domain.model.nutrition.extra_nutrients import extra_nutrients_to_micros
from src.domain.services.ai_output_validation_service import (
    validate_ai_output,
    validate_parse_text_nutrition,
)
from src.domain.services.nutrition_calculation_service import (
    _convert_with_allowed_units,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityPolicy

logger = logging.getLogger(__name__)
CACHE_VERSION = 3
CACHE_TIMEOUT_SECONDS = 0.05


def parse_text_cache_key(text: str, language: str | None, context: str) -> str:
    normalized = " ".join(text.strip().lower().split())
    digest = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    context_hash = hashlib.sha256(context.encode()).hexdigest()[:16]
    return f"parse_text:v{CACHE_VERSION}:{language or 'en'}:{context_hash}:{digest}"


def response_from_cache(data: Any) -> ParseMealTextResponseDto:
    """Validate cache hits as strictly as new portions and retain all DTO fields."""
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        raise ValueError("incompatible parse-text cache")
    body = data["response"]
    if body.get("unmatched_terms"):
        raise ValueError("partial parse-text response cannot be cached")
    items = [ParsedFoodItemDto(**item) for item in body["items"]]
    if not items:
        raise ValueError("empty parse-text response cannot be cached")
    portions = []
    for item in items:
        grams = _convert_with_allowed_units(
            item.quantity, item.unit, item.allowed_units, item.name
        )
        macros = {
            f"{key}_g": getattr(item, key)
            for key in ("protein", "carbs", "fat", "fiber", "sugar")
        }
        extras = (item.source_snapshot or {}).get("extra_nutrients")
        if extras is not None and not isinstance(extras, dict):
            raise ValueError("invalid parse-text micronutrient snapshot")
        if extras is not None:
            for value in extras.values():
                amount = value.get("amount") if isinstance(value, dict) else value
                if value is None:
                    continue
                if (
                    isinstance(amount, bool)
                    or not isinstance(amount, (int, float))
                    or not math.isfinite(amount)
                    or amount < 0
                ):
                    raise ValueError("invalid parse-text micronutrient snapshot")
                if (
                    isinstance(value, dict)
                    and value.get("unit") is not None
                    and not isinstance(value.get("unit"), str)
                ):
                    raise ValueError("invalid parse-text micronutrient snapshot")
        portion_micros = None
        if extras:
            converted = extra_nutrients_to_micros(extras, factor=grams / 100)
            portion_micros = converted.to_dict() if converted is not None else None
        portion = {
            "name": item.name,
            "quantity": item.quantity,
            "unit": item.unit,
            "quantity_g": grams,
            "macros": macros,
            "micros": portion_micros,
        }
        portions.append(portion)
        if item.source_snapshot is not None:
            _require_finite_snapshot(item.source_snapshot)
        _validate_item_snapshot(item, grams)
    validated = validate_ai_output(
        {"items": portions},
        schema=MealTextNutritionResponse,
        purpose="parse_text",
        attempt_count=1,
    )
    validate_parse_text_nutrition(validated)
    for nutrient in ("protein", "carbs", "fat"):
        total = body[f"total_{nutrient}"]
        if not math.isfinite(total) or not math.isclose(
            total, sum(getattr(item, nutrient) for item in items), abs_tol=0.0001
        ):
            raise ValueError("inconsistent parse-text cache totals")
    return ParseMealTextResponseDto(**{**body, "items": items})


def _validate_item_snapshot(item: ParsedFoodItemDto, grams: float) -> None:
    snapshot = item.source_snapshot
    if not isinstance(snapshot, dict):
        raise ValueError("parse-text item has no nutrition snapshot")
    density_fields = (
        "protein_per_100g",
        "carbs_per_100g",
        "fat_per_100g",
        "fiber_per_100g",
        "sugar_per_100g",
        "calories_per_100g",
    )
    if any(getattr(item, key) is None for key in density_fields):
        raise ValueError("parse-text item has incomplete density")
    for key in density_fields:
        value = getattr(item, key)
        if not math.isfinite(value) or value < 0 or snapshot.get(key) != value:
            raise ValueError("parse-text snapshot disagrees with its item")
    for absolute, density in (
        (item.protein, item.protein_per_100g),
        (item.carbs, item.carbs_per_100g),
        (item.fat, item.fat_per_100g),
        (item.fiber, item.fiber_per_100g),
        (item.sugar, item.sugar_per_100g),
    ):
        if not math.isclose(absolute, density * grams / 100, abs_tol=0.15):
            raise ValueError("parse-text portion disagrees with density")
    if snapshot.get("basis") != "100g":
        raise ValueError("unsupported parse-text nutrition basis")
    if snapshot.get("origin") != item.origin:
        raise ValueError("parse-text snapshot origin disagrees")
    if (
        snapshot.get("source_namespace") != item.source_namespace
        or snapshot.get("source_food_id") != item.source_food_id
    ):
        raise ValueError("parse-text snapshot identity disagrees")
    if snapshot.get("allowed_units") != (item.allowed_units or []):
        raise ValueError("parse-text snapshot serving units disagree")
    origin_fields = {
        "food_reference_id": item.food_reference_id,
        "fdc_id": item.fdc_id,
        "origin": item.origin,
        "source": item.origin or item.data_source,
        "provider_food_id": (
            item.source_food_id or item.food_id
            if item.origin in {"provider", "fatsecret"}
            else None
        ),
    }
    from src.domain.services.nutrition_integrity_policy import normalize_logical_origin

    origin_result = normalize_logical_origin(origin_fields)
    if not origin_result.accepted:
        raise ValueError("parse-text item has invalid provenance")
    integrity = NutritionIntegrityPolicy().evaluate(
        {
            "protein_100g": item.protein_per_100g,
            "carbs_100g": item.carbs_per_100g,
            "fat_100g": item.fat_per_100g,
            "fiber_100g": item.fiber_per_100g,
            "sugar_100g": item.sugar_per_100g,
            "calories_100g": item.calories_per_100g,
            "allowed_units": item.allowed_units,
        },
        origin_fields=origin_fields,
    )
    if not integrity.accepted:
        raise ValueError("parse-text density fails nutrition integrity")


def _require_finite_snapshot(value: Any) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _require_finite_snapshot(child)
    elif isinstance(value, list):
        for child in value:
            _require_finite_snapshot(child)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite parse-text snapshot")


async def read_parse_text_cache(
    cache: Any, key: str, *, timeout_seconds: float = CACHE_TIMEOUT_SECONDS
) -> ParseMealTextResponseDto | None:
    if cache is None or timeout_seconds <= 0:
        return None
    try:
        data = await asyncio.wait_for(cache.get(key), timeout=timeout_seconds)
        return response_from_cache(data) if data is not None else None
    except Exception as exc:
        logger.debug("parse_text cache read ignored: %s", type(exc).__name__)
        return None


async def write_parse_text_cache(
    cache: Any,
    key: str,
    response: ParseMealTextResponseDto,
    ttl_seconds: int,
    *,
    timeout_seconds: float = CACHE_TIMEOUT_SECONDS,
) -> None:
    if cache is None or timeout_seconds <= 0:
        return
    try:
        data = {"version": CACHE_VERSION, "response": asdict(response)}
        response_from_cache(data)
        await asyncio.wait_for(
            cache.set(key, data, ttl_seconds=ttl_seconds), timeout=timeout_seconds
        )
    except Exception as exc:
        logger.debug("parse_text cache write ignored: %s", type(exc).__name__)
