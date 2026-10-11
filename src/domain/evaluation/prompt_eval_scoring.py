from __future__ import annotations

from statistics import mean, median, pstdev
from typing import Any

from src.domain.parsers.vision_response_parser import (
    GPTResponseParsingError,
    VisionResponseParser,
)

from .prompt_eval_metrics import (
    append_optional,
    interval_error,
    meal_macros,
    percentile,
    score_foods,
    score_micro_references,
)
from .prompt_eval_models import (
    MACRO_FIELDS,
    PromptEvalCase,
    PromptEvalResult,
)
from .prompt_eval_observations import observations_for


def score_candidate(
    parser: VisionResponseParser,
    name: str,
    prompt: str,
    cases: list[PromptEvalCase],
    candidate_results: dict[str, Any],
) -> PromptEvalResult:
    valid_count = parse_count = 0
    reviewed_case_count = 0
    identity_case_count = 0
    food_detection_correct = food_detection_count = 0
    evidence_kinds: list[str] = []
    true_positive = false_positive = false_negative = 0
    portion_errors: list[float] = []
    calorie_errors: list[float] = []
    macro_errors: dict[str, list[float]] = {field: [] for field in MACRO_FIELDS}
    micro_hits: dict[str, int] = {}
    micro_counts: dict[str, int] = {}
    micro_errors: dict[str, list[float]] = {}
    unknown_values: dict[str, int] = {}
    calories_by_case: dict[str, list[float]] = {}
    durations: list[float] = []
    input_tokens: list[float] = []
    output_tokens: list[float] = []
    cached_tokens: list[float] = []
    routes: set[str] = set()
    observation_count = 0

    for case in cases:
        supplied = candidate_results.get(case.case_id)
        observations = observations_for(supplied, case.response_payload)
        for observation in observations:
            observation_count += 1
            evidence_kinds.append(observation.evidence_kind)
            append_optional(durations, observation.duration_ms)
            append_optional(input_tokens, observation.input_tokens)
            append_optional(output_tokens, observation.output_tokens)
            append_optional(cached_tokens, observation.cached_tokens)
            if observation.model_route:
                routes.add(observation.model_route)
            payload = observation.response_payload
            structured = payload.get("structured_data", {})
            try:
                parser.validate_structured_data(structured)
                valid_count += 1
            except GPTResponseParsingError:
                continue
            try:
                nutrition = parser.parse_to_nutrition(payload)
            except GPTResponseParsingError:
                continue
            parse_count += 1
            foods = nutrition.food_items or []
            calories_by_case.setdefault(case.case_id, []).append(
                nutrition.macros.total_calories
            )
            if case.expected_foods:
                identity_case_count += 1
            if case.expected_is_food is not None:
                food_detection_count += 1
                food_detection_correct += bool(foods) == case.expected_is_food
            if (
                case.expected_foods
                or case.expected_calories_kcal is not None
                or case.expected_is_food is not None
            ):
                reviewed_case_count += 1
                tp, fp, fn, grams = score_foods(case.expected_foods, foods)
                true_positive += tp
                false_positive += fp
                false_negative += fn
                portion_errors.extend(grams)
                if case.expected_calories_kcal is not None:
                    calorie_errors.append(
                        interval_error(
                            nutrition.macros.total_calories,
                            case.expected_calories_kcal,
                        )
                    )
                actual_macros = meal_macros(nutrition.macros)
                for field_name, expected in case.expected_macros.items():
                    if field_name in actual_macros:
                        macro_errors.setdefault(field_name, []).append(
                            interval_error(actual_macros[field_name], expected)
                        )
                score_micro_references(
                    structured,
                    case.expected_micros,
                    micro_counts,
                    micro_hits,
                    micro_errors,
                    unknown_values,
                )

    parse_rate = parse_count / max(observation_count, 1)
    validation_rate = valid_count / max(observation_count, 1)
    provider_quality = (
        reviewed_case_count > 0
        and bool(evidence_kinds)
        and all(kind == "provider_observation" for kind in evidence_kinds)
    )
    provider_latency = bool(evidence_kinds) and all(
        kind == "provider_observation" for kind in evidence_kinds
    )
    repeated = [values for values in calories_by_case.values() if len(values) > 1]
    return PromptEvalResult(
        name=name,
        parse_success_rate=parse_rate,
        validation_success_rate=validation_rate,
        prompt_tokens_estimate=len(prompt) / 4.0,
        score=(parse_rate * 100.0) - (len(prompt) / 400.0),
        evidence_kind="provider_observation"
        if provider_quality
        else "offline_contract_only",
        case_count=len(cases),
        food_detection_accuracy=(
            food_detection_correct / food_detection_count
            if provider_quality and food_detection_count
            else None
        ),
        identity_precision=(true_positive / max(true_positive + false_positive, 1))
        if provider_quality and identity_case_count
        else None,
        identity_recall=(true_positive / max(true_positive + false_negative, 1))
        if provider_quality and identity_case_count
        else None,
        portion_mae_g=mean(portion_errors)
        if provider_quality and portion_errors
        else None,
        calories_mae_kcal=mean(calorie_errors)
        if provider_quality and calorie_errors
        else None,
        macro_mae_g={
            key: mean(values)
            for key, values in macro_errors.items()
            if provider_quality and values
        },
        micro_coverage={
            key: micro_hits.get(key, 0) / count for key, count in micro_counts.items()
        }
        if provider_quality
        else {},
        micro_normalized_mae={
            key: mean(values)
            for key, values in micro_errors.items()
            if provider_quality and values
        },
        unknown_micro_population=unknown_values if provider_quality else {},
        repeatability_calorie_sd_kcal=(
            median([pstdev(values) for values in repeated])
            if provider_quality and repeated
            else None
        ),
        latency_p50_ms=median(durations) if durations and provider_latency else None,
        latency_p95_ms=(
            percentile(durations, 0.95) if durations and provider_latency else None
        ),
        input_tokens=sum(input_tokens) if provider_quality and input_tokens else None,
        output_tokens=sum(output_tokens)
        if provider_quality and output_tokens
        else None,
        cached_tokens=sum(cached_tokens)
        if provider_quality and cached_tokens
        else None,
        model_routes=tuple(sorted(routes)),
    )
