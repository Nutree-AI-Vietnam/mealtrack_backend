"""Aggregate raw-model, handler-final, and final-output micro metrics."""

from __future__ import annotations

from statistics import mean
from typing import Any

from .meal_text_nutrition_eval_models import ParseTextEvalCaseResult


def aggregate_micro_metrics(
    results: list[ParseTextEvalCaseResult], provider_quality: bool
) -> dict[str, Any]:
    final_coverage: dict[str, list[float]] = {}
    final_errors: dict[str, list[float]] = {}
    raw_coverage: dict[str, list[float]] = {}
    raw_errors: dict[str, list[float]] = {}
    handler_coverage: dict[str, list[float]] = {}
    handler_errors: dict[str, list[float]] = {}
    unknown_counts: dict[str, int] = {}
    for result in results:
        _append(final_coverage, result.micro_coverage)
        _append(final_errors, result.micro_normalized_mae)
        _append(raw_coverage, result.raw_model_micro_coverage)
        _append(raw_errors, result.raw_model_micro_normalized_mae)
        if result.handler_food_count is not None:
            _append(handler_coverage, result.handler_micro_coverage)
            _append(handler_errors, result.handler_micro_normalized_mae)
        for name, count in result.unknown_micro_population.items():
            unknown_counts[name] = unknown_counts.get(name, 0) + count

    final_cov = _means(final_coverage) if provider_quality else {}
    final_mae = _means(final_errors) if provider_quality else {}
    return {
        "micro_coverage": final_cov,
        "micro_normalized_mae": final_mae,
        "final_output_micro_coverage": final_cov,
        "final_output_micro_normalized_mae": final_mae,
        "handler_micro_coverage": _means(handler_coverage) if provider_quality else {},
        "handler_micro_normalized_mae": (
            _means(handler_errors) if provider_quality else {}
        ),
        "raw_model_micro_coverage": _means(raw_coverage) if provider_quality else {},
        "raw_model_micro_normalized_mae": (
            _means(raw_errors) if provider_quality else {}
        ),
        "unknown_micro_population": unknown_counts if provider_quality else {},
    }


def _append(target: dict[str, list[float]], values: dict[str, float]) -> None:
    for name, value in values.items():
        target.setdefault(name, []).append(value)


def _means(values: dict[str, list[float]]) -> dict[str, float]:
    return {name: mean(samples) for name, samples in values.items()}
