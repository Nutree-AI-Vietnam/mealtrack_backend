from __future__ import annotations

from statistics import mean, median, pstdev

from .meal_text_nutrition_eval_aggregate_micros import aggregate_micro_metrics
from .meal_text_nutrition_eval_aggregate_timing import aggregate_timing_metrics
from .meal_text_nutrition_eval_models import (
    COMMON_REFERENCE_SOURCES,
    ParseTextEvalCase,
    ParseTextEvalCaseResult,
    ParseTextEvalObservation,
    ParseTextEvalSummary,
)


def build_summary(
    cases: list[ParseTextEvalCase],
    observations: list[ParseTextEvalObservation],
    results: list[ParseTextEvalCaseResult],
) -> ParseTextEvalSummary:
    common_results = [
        result
        for case, result in zip(cases, results, strict=True)
        if case.reference_source_required
        and case.expected_source in COMMON_REFERENCE_SOURCES
    ]
    provider_quality = bool(observations) and all(
        observation.evidence_kind == "provider_observation"
        for observation in observations
    )
    evidence_kinds = {observation.evidence_kind for observation in observations}
    evidence_label = (
        next(iter(evidence_kinds)) if len(evidence_kinds) == 1 else "mixed_evidence"
    )
    calorie_groups: dict[str, list[float]] = {}
    for result in results:
        calorie_groups.setdefault(result.case_id, []).append(result.total_calories_kcal)
    repeated = [values for values in calorie_groups.values() if len(values) > 1]
    macro_values: dict[str, list[float]] = {}
    raw_macro_values: dict[str, list[float]] = {}
    handler_macro_values: dict[str, list[float]] = {}
    raw_calorie_errors = [
        result.raw_model_calories_error_kcal
        for result in results
        if result.raw_model_calories_error_kcal is not None
    ]
    handler_calorie_errors = [
        result.handler_calories_error_kcal
        for result in results
        if result.handler_calories_error_kcal is not None
    ]
    for result in results:
        for name, value in result.macro_mae_g.items():
            macro_values.setdefault(name, []).append(value)
        for name, value in result.raw_model_macro_mae_g.items():
            raw_macro_values.setdefault(name, []).append(value)
        if result.handler_food_count is not None:
            for name, value in result.macro_mae_g.items():
                handler_macro_values.setdefault(name, []).append(value)
    final_calories = (
        _mean(
            [
                result.calories_error_kcal
                for result in results
                if result.calories_error_kcal is not None
            ]
        )
        if provider_quality
        else None
    )
    final_macros = (
        {name: mean(values) for name, values in macro_values.items()}
        if provider_quality
        else {}
    )
    handler_calories = _mean(handler_calorie_errors) if provider_quality else None
    handler_macros = (
        {name: mean(values) for name, values in handler_macro_values.items()}
        if provider_quality
        else {}
    )
    return ParseTextEvalSummary(
        case_count=len(results),
        contract_pass_rate=_rate(results, "contract_pass"),
        identity_quantity_pass_rate=sum(
            result.identity_pass and result.quantity_pass for result in results
        )
        / len(results),
        candidate_pass_rate=_rate(results, "candidate_pass"),
        common_reference_pass_rate=(
            _rate(common_results, "reference_pass") if common_results else 1.0
        ),
        catastrophic_outliers=sum(result.catastrophic_outlier for result in results),
        invalid_reference_accepts=sum(
            result.source == "fatsecret" and not result.reference_pass
            for result in results
        ),
        provider_calls=tuple(result.provider_calls for result in results),
        cases=tuple(results),
        fallback_rate=_rate(results, "fallback_used"),
        provider_search_calls=tuple(result.provider_search_calls for result in results),
        provider_detail_calls=tuple(result.provider_detail_calls for result in results),
        evidence_kind=evidence_label,
        final_output_kinds=tuple(
            sorted({obs.final_output_kind for obs in observations})
        ),
        portion_mae_g=_mean(
            [r.portion_mae_g for r in results if r.portion_mae_g is not None]
        )
        if provider_quality
        else None,
        calories_mae_kcal=final_calories,
        final_output_calories_mae_kcal=final_calories,
        macro_mae_g=final_macros,
        final_output_macro_mae_g=final_macros,
        handler_calories_mae_kcal=handler_calories,
        handler_macro_mae_g=handler_macros,
        raw_model_calories_mae_kcal=_mean(raw_calorie_errors)
        if provider_quality
        else None,
        raw_model_macro_mae_g={
            name: mean(values) for name, values in raw_macro_values.items()
        }
        if provider_quality
        else {},
        repeatability_calorie_sd_kcal=(
            median([pstdev(values) for values in repeated])
            if repeated and provider_quality
            else None
        ),
        input_tokens=_sum_all_optional(result.input_tokens for result in results)
        if provider_quality
        else None,
        output_tokens=_sum_all_optional(result.output_tokens for result in results)
        if provider_quality
        else None,
        cached_tokens=_sum_all_optional(result.cached_tokens for result in results)
        if provider_quality
        else None,
        model_routes=tuple(
            sorted({result.model_route for result in results if result.model_route})
        ),
        **aggregate_micro_metrics(results, provider_quality),
        **aggregate_timing_metrics(results, observations, provider_quality),
    )


def _rate(results: list[ParseTextEvalCaseResult], field_name: str) -> float:
    return sum(bool(getattr(result, field_name)) for result in results) / len(results)


def _mean(values: list[float | None]) -> float | None:
    actual = [float(value) for value in values if value is not None]
    return mean(actual) if actual else None


def _sum_all_optional(values) -> float | None:
    actual = list(values)
    if not actual or any(value is None for value in actual):
        return None
    return sum(float(value) for value in actual)
