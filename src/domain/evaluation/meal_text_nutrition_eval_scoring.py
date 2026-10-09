from __future__ import annotations

from statistics import mean

from src.domain.model.nutrition.macros import Macros

from .meal_text_nutrition_eval_items import score_items
from .meal_text_nutrition_eval_metrics import (
    any_effective_micro_present,
    any_micro_present,
    interval_error,
    score_micro_references,
    sum_complete_micros,
    sum_effective_micros,
    sum_item_macros,
    sum_raw_macros,
)
from .meal_text_nutrition_eval_models import (
    ParseTextEvalCase,
    ParseTextEvalCaseResult,
    ParseTextEvalObservation,
)


def evaluate_case(
    case: ParseTextEvalCase, observation: ParseTextEvalObservation
) -> ParseTextEvalCaseResult:
    response_items = list(getattr(observation.response, "items", []) or [])
    raw_items = list(observation.extracted_items)
    expected_items = list(case.expected_items) or [
        {
            "aliases": [case.expected_lookup_name],
            "quantity_g": case.expected_quantity_g,
            "candidate_id": case.expected_candidate_id,
            "source": case.expected_source,
        }
    ]
    if not raw_items:
        raw_items = [
            {
                "lookup_name": getattr(item, "canonical_name", None)
                or getattr(item, "name", None)
                or observation.extracted_lookup_name,
                "quantity_g": getattr(item, "quantity", None)
                or observation.extracted_quantity_g,
                "micros": None,
            }
            for item in response_items
        ]
        if not raw_items and observation.extracted_lookup_name:
            raw_items = [
                {
                    "lookup_name": observation.extracted_lookup_name,
                    "quantity_g": observation.extracted_quantity_g,
                    "micros": None,
                }
            ]
    identity_pass, quantity_pass, candidate_pass, portion_errors = score_items(
        expected_items,
        raw_items,
        observation.selected_candidate_ids,
        observation.selected_candidate_id,
    )
    sources = [getattr(item, "data_source", None) for item in response_items]
    expected_sources = {
        str(item.get("source") or case.expected_source) for item in expected_items
    }
    reference_pass = (
        True
        if not case.reference_source_required
        else bool(sources) and all(source in expected_sources for source in sources)
    )
    contract_pass = bool(response_items) and all(
        isinstance(source, str) for source in sources
    )
    total_calories = sum(
        float(getattr(item, "calories", 0.0) or 0.0) for item in response_items
    )
    low, high = case.expected_calorie_range
    catastrophic = not low <= total_calories <= high
    calories_error = interval_error(total_calories, (low, high))
    macros = sum_item_macros(response_items)
    macro_errors = {
        name: interval_error(macros[name], reference)
        for name, reference in case.expected_meal_macros.items()
        if name in macros
    }
    is_handler_output = observation.final_output_kind.startswith("handler_final")
    raw_coverage, raw_errors, unknown_population = score_micro_references(
        case.expected_meal_micros,
        sum_complete_micros(raw_items),
        lambda name: any_micro_present(raw_items, name),
    )
    final_coverage, final_errors, final_unknown = score_micro_references(
        case.expected_meal_micros,
        sum_effective_micros(response_items),
        lambda name: any_effective_micro_present(response_items, name),
    )
    for name, count in final_unknown.items():
        unknown_population[name] = max(unknown_population.get(name, 0), count)
    raw_macros = sum_raw_macros(raw_items)
    raw_macro_errors = {
        name: interval_error(raw_macros[name], reference)
        for name, reference in case.expected_meal_macros.items()
        if name in raw_macros
    }
    raw_calories = Macros.raw_total_calories(
        raw_macros["protein_g"],
        raw_macros["carbs_g"],
        raw_macros["fat_g"],
        raw_macros["fiber_g"],
    )
    reasons = tuple(
        name
        for passed, name in (
            (identity_pass, "identity"),
            (quantity_pass, "quantity"),
            (candidate_pass, "candidate"),
            (reference_pass, "source"),
            (not catastrophic, "calorie_outlier"),
        )
        if not passed
    )
    return ParseTextEvalCaseResult(
        case_id=case.case_id,
        contract_pass=contract_pass,
        identity_pass=identity_pass,
        quantity_pass=quantity_pass,
        candidate_pass=candidate_pass,
        reference_pass=reference_pass,
        catastrophic_outlier=catastrophic,
        source=sources[0] if sources else None,
        provider_calls=observation.provider_searches + observation.provider_details,
        duration_ms=observation.duration_ms,
        reason_codes=reasons,
        provider_search_calls=observation.provider_searches,
        provider_detail_calls=observation.provider_details,
        fallback_used=case.reference_source_required
        and any(source == "ai_estimate" for source in sources),
        food_count=len(response_items),
        total_calories_kcal=total_calories,
        calories_error_kcal=calories_error,
        portion_mae_g=mean(portion_errors) if portion_errors else None,
        macro_mae_g=macro_errors,
        handler_food_count=len(response_items) if is_handler_output else None,
        handler_total_calories_kcal=total_calories if is_handler_output else None,
        handler_calories_error_kcal=calories_error if is_handler_output else None,
        raw_model_food_count=len(raw_items),
        raw_model_calories_error_kcal=interval_error(raw_calories, (low, high)),
        raw_model_macro_mae_g=raw_macro_errors,
        raw_model_micro_coverage=raw_coverage,
        raw_model_micro_normalized_mae=raw_errors,
        micro_coverage=final_coverage,
        micro_normalized_mae=final_errors,
        handler_micro_coverage=final_coverage if is_handler_output else {},
        handler_micro_normalized_mae=final_errors if is_handler_output else {},
        unknown_micro_population=unknown_population,
        evidence_kind=observation.evidence_kind,
        final_output_kind=observation.final_output_kind,
        generation_duration_ms=observation.generation_duration_ms,
        input_tokens=observation.input_tokens,
        output_tokens=observation.output_tokens,
        cached_tokens=observation.cached_tokens,
        model_route=observation.model_route,
    )
