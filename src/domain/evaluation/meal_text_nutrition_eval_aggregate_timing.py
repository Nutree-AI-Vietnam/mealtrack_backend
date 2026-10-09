"""Aggregate fixture, handler, and provider-generation timing separately."""

from __future__ import annotations

from statistics import median

from .meal_text_nutrition_eval_models import (
    ParseTextEvalCaseResult,
    ParseTextEvalObservation,
)


def aggregate_timing_metrics(
    results: list[ParseTextEvalCaseResult],
    observations: list[ParseTextEvalObservation],
    provider_quality: bool,
) -> dict[str, float | None]:
    durations = [result.duration_ms for result in results]
    offline_durations = [
        result.duration_ms
        for result in results
        if result.evidence_kind == "offline_contract_only"
    ]
    handler_durations = [
        result.duration_ms
        for result in results
        if result.final_output_kind.startswith("handler_final")
    ]
    generation_durations = [
        result.generation_duration_ms
        for result in results
        if result.generation_duration_ms is not None
    ]
    offline_only = {observation.evidence_kind for observation in observations} == {
        "offline_contract_only"
    }
    provider_generation = (
        _timings(generation_durations) if provider_quality else (None, None)
    )
    return {
        "latency_p50_ms": round(median(durations), 3) if provider_quality else None,
        "latency_p95_ms": round(_percentile(durations, 0.95), 3)
        if provider_quality
        else None,
        "offline_fixture_latency_p50_ms": round(median(offline_durations), 3)
        if offline_only and offline_durations
        else None,
        "offline_fixture_latency_p95_ms": round(_percentile(offline_durations, 0.95), 3)
        if offline_only and offline_durations
        else None,
        "handler_end_to_end_latency_p50_ms": round(median(handler_durations), 3)
        if handler_durations
        else None,
        "handler_end_to_end_latency_p95_ms": round(
            _percentile(handler_durations, 0.95), 3
        )
        if handler_durations
        else None,
        "generation_latency_p50_ms": provider_generation[0],
        "generation_latency_p95_ms": provider_generation[1],
        "provider_generation_latency_p50_ms": provider_generation[0],
        "provider_generation_latency_p95_ms": provider_generation[1],
    }


def _timings(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return round(median(values), 3), round(_percentile(values, 0.95), 3)


def _percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile_value)))
    return ordered[index]
