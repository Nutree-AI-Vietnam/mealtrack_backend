"""Score live accuracy-baseline predictions against QA gold intervals.

Gold is a point or a closed interval. Absolute error is zero inside the
interval and the distance to the nearest bound outside it. MAPE uses that
error divided by the absolute midpoint, and is omitted when the midpoint is
0. A zero midpoint is inside ±10% and ±20% only when the error is zero.
"""

from __future__ import annotations

import os
from typing import Any

from src.domain.evaluation.prompt_eval_metrics import (
    interval_error,
    midpoint,
    percentile,
)

NUTRIENTS = ("kcal", "protein_g", "carbs_g", "fat_g")
_WITHIN = (("within_10", 0.10), ("within_20", 0.20))


def gold_bounds(case: dict[str, Any], nutrient: str) -> tuple[float, float] | None:
    """Return the QA interval for one nutrient, or None when it is unlabeled."""
    if case.get("label_status") == "unscored":
        return None
    if nutrient == "kcal":
        return _as_bounds(case.get("expected_calories_kcal"))
    macros = case.get("expected_macros") or {}
    if not isinstance(macros, dict) or nutrient not in macros:
        return None
    return _as_bounds(macros.get(nutrient))


def score_nutrient(
    predicted: float | None,
    bounds: tuple[float, float] | None,
    *,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    """Score one predicted nutrient. Unlabeled and failed cases stay unscored."""
    if blocked_reason is not None:
        return {"scored": False, "reason": blocked_reason}
    if bounds is None:
        return {"scored": False, "reason": "no_gold"}
    if predicted is None:
        return {"scored": False, "reason": "no_prediction"}
    error = float(interval_error(float(predicted), bounds))
    center = float(midpoint(bounds))
    percentage = None if center == 0 else error / abs(center)
    scored: dict[str, Any] = {
        "scored": True,
        "absolute_error": error,
        "percentage_error": percentage,
        "predicted": float(predicted),
        "gold_low": bounds[0],
        "gold_high": bounds[1],
    }
    for name, tolerance in _WITHIN:
        if percentage is None:
            scored[name] = error == 0
        else:
            scored[name] = percentage <= tolerance
    return scored


def score_case(
    case: dict[str, Any],
    *,
    predicted: dict[str, float] | None,
    error_type: str | None,
    duration_ms: float,
    exceeded_product_deadline: bool = False,
) -> dict[str, Any]:
    """Score kcal, protein, carbs, and fat for one labeled case."""
    if error_type == "NotFood":
        blocked = "not_food"
        outcome = "not_food"
    elif error_type:
        blocked = "provider_error"
        outcome = "provider_error"
    else:
        blocked = None
        outcome = None
    nutrients = {
        nutrient: score_nutrient(
            None if predicted is None else predicted.get(nutrient),
            gold_bounds(case, nutrient),
            blocked_reason=blocked,
        )
        for nutrient in NUTRIENTS
    }
    if outcome is None:
        if case.get("label_status") == "unscored":
            outcome = "unscored_label"
        elif any(item["scored"] for item in nutrients.values()):
            outcome = "partial" if case.get("label_status") == "partial" else "scored"
        else:
            outcome = "unscored_label"
    return {
        "case_id": case["case_id"],
        "duration_ms": float(duration_ms),
        "error_type": error_type,
        "exceeded_product_deadline": exceeded_product_deadline,
        "label_status": case.get("label_status"),
        "nutrients": nutrients,
        "outcome": outcome,
        "split": case.get("split"),
    }


def aggregate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean absolute error, MAPE, and tolerance rates per nutrient."""
    nutrients: dict[str, Any] = {}
    for nutrient in NUTRIENTS:
        scored = [
            case["nutrients"][nutrient]
            for case in cases
            if case["nutrients"][nutrient]["scored"]
        ]
        percentages = [
            float(item["percentage_error"])
            for item in scored
            if item["percentage_error"] is not None
        ]
        nutrients[nutrient] = {
            "mae": _mean([float(item["absolute_error"]) for item in scored]),
            "mape": _mean(percentages),
            "mape_cases": len(percentages),
            "scored_cases": len(scored),
            "within_10_cases": sum(1 for item in scored if item["within_10"]),
            "within_10_rate": _rate(scored, "within_10"),
            "within_20_cases": sum(1 for item in scored if item["within_20"]),
            "within_20_rate": _rate(scored, "within_20"),
        }
    durations = [float(case["duration_ms"]) for case in cases]
    return {
        "case_count": len(cases),
        "latency_ms": _latency(durations),
        "not_food_cases": sum(1 for case in cases if case["outcome"] == "not_food"),
        "nutrients": nutrients,
        "partial_cases": sum(1 for case in cases if case["outcome"] == "partial"),
        "provider_error_cases": sum(
            1 for case in cases if case["outcome"] == "provider_error"
        ),
        "scored_cases": sum(1 for case in cases if case["outcome"] == "scored"),
        "unscored_label_cases": sum(
            1 for case in cases if case["outcome"] == "unscored_label"
        ),
    }


def summarize_grounding_items(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count local-catalog misses and lookup-key mismatches.

    ``product_local_hit`` is whether the batch result contained either
    ``normalize_food_lookup_name`` or ``normalize_food_name``. Meal scan and
    parse-text query both. Seeds store the second key on ``name_normalized``.
    ``catalog_key_would_hit`` is that seed key for rows that missed both.
    """
    mismatch_misses = [
        row
        for row in rows
        if row.get("keys_differ") and not row.get("product_local_hit")
    ]
    diagnostics = [row.get("catalog_key_would_hit") for row in mismatch_misses]
    known = [value for value in diagnostics if isinstance(value, bool)]
    if not mismatch_misses:
        diagnostic = "not_needed"
        would_hit: int | None = 0
    elif len(known) != len(diagnostics):
        diagnostic = "unavailable"
        would_hit = None
    else:
        diagnostic = "queried"
        would_hit = sum(1 for value in known if value)
    tiers: dict[str, int] = {}
    for row in rows:
        tier = str(row.get("tier") or "unknown")
        tiers[tier] = tiers.get(tier, 0) + 1
    return {
        "catalog_key_diagnostic": diagnostic,
        "items": len(rows),
        "key_mismatch_and_local_miss": len(mismatch_misses),
        "key_mismatch_catalog_key_would_hit": would_hit,
        "key_mismatch_items": sum(1 for row in rows if row.get("keys_differ")),
        "local_hits": sum(1 for row in rows if row.get("product_local_hit")),
        "local_misses": sum(1 for row in rows if not row.get("product_local_hit")),
        "tiers": tiers,
    }


def sum_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum provider usage records. Missing metadata contributes zero tokens."""
    by_model: dict[str, dict[str, int]] = {}
    for record in records:
        model = str(record.get("model") or "unknown")
        slot = by_model.setdefault(
            model,
            {"cached_tokens": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0},
        )
        slot["calls"] += 1
        slot["input_tokens"] += int(record.get("input_tokens") or 0)
        slot["output_tokens"] += int(record.get("output_tokens") or 0)
        slot["cached_tokens"] += int(record.get("cached_tokens") or 0)
    return {
        "by_model": by_model,
        "cached_tokens": sum(slot["cached_tokens"] for slot in by_model.values()),
        "calls": sum(slot["calls"] for slot in by_model.values()),
        "input_tokens": sum(slot["input_tokens"] for slot in by_model.values()),
        "output_tokens": sum(slot["output_tokens"] for slot in by_model.values()),
    }


def estimate_usd(usage: dict[str, Any], *, fatsecret_calls: int) -> dict[str, Any]:
    """Price tokens only when the caller supplies rates. Never invent a list price."""
    input_rate = _optional_rate("ACCURACY_BASELINE_INPUT_USD_PER_MILLION")
    output_rate = _optional_rate("ACCURACY_BASELINE_OUTPUT_USD_PER_MILLION")
    fatsecret_rate = _optional_rate("ACCURACY_BASELINE_FATSECRET_USD_PER_CALL")
    token_usd = None
    if input_rate is not None and output_rate is not None:
        token_usd = (
            usage["input_tokens"] / 1_000_000 * input_rate
            + usage["output_tokens"] / 1_000_000 * output_rate
        )
    fatsecret_usd = None if fatsecret_rate is None else fatsecret_calls * fatsecret_rate
    usd = None
    if token_usd is not None or fatsecret_usd is not None:
        usd = (token_usd or 0.0) + (fatsecret_usd or 0.0)
    if usd is None:
        method = (
            "Token totals are copied from provider usage_metadata when the "
            "OpenAI adapter records it. USD is null because "
            "ACCURACY_BASELINE_INPUT_USD_PER_MILLION and "
            "ACCURACY_BASELINE_OUTPUT_USD_PER_MILLION were not set, and this "
            "repo does not hard-code a model price. FatSecret searches and "
            "detail calls are counted. They have no USD figure unless "
            "ACCURACY_BASELINE_FATSECRET_USD_PER_CALL is set. Cached tokens "
            "are included in input_tokens and are not priced separately."
        )
    else:
        method = (
            "USD uses the ACCURACY_BASELINE_* rates from the environment. "
            "Cached tokens stay inside input_tokens. Unset FatSecret or token "
            "rates contribute nothing to the sum."
        )
    return {
        "fatsecret_calls": fatsecret_calls,
        "method": method,
        "token_usd": token_usd,
        "usd": usd,
    }


def _as_bounds(value: Any) -> tuple[float, float] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return (number, number)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        low = float(value[0])
        high = float(value[1])
        if high < low:
            low, high = high, low
        return (low, high)
    return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _rate(scored: list[dict[str, Any]], field: str) -> float | None:
    if not scored:
        return None
    return sum(1 for item in scored if item[field]) / len(scored)


def _latency(durations: list[float]) -> dict[str, float | int | None]:
    if not durations:
        return {
            "cases": 0,
            "max_ms": None,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
        }
    return {
        "cases": len(durations),
        "max_ms": max(durations),
        "mean_ms": sum(durations) / len(durations),
        "p50_ms": percentile(durations, 0.50),
        "p95_ms": percentile(durations, 0.95),
    }


def _optional_rate(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value < 0:
        return None
    return value
