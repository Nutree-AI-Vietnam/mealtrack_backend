from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

COMMON_REFERENCE_SOURCES = {"usda", "fatsecret"}
TEXT_MICRO_UNITS = {
    "vitamin_a": "mcg",
    "vitamin_c": "mg",
    "vitamin_e": "mg",
    "calcium": "mg",
    "iron": "mg",
    "magnesium": "mg",
    "potassium": "mg",
    "sodium": "mg",
    "saturated_fat": "g",
    "added_sugar": "g",
}
TEXT_MICRO_FLOORS = {
    **{
        name: 0.1
        for name in TEXT_MICRO_UNITS
        if name not in {"vitamin_a", "saturated_fat", "added_sugar"}
    },
    "vitamin_a": 1.0,
    "saturated_fat": 0.01,
    "added_sugar": 0.01,
}
TEXT_MACROS = ("protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g")


@dataclass(frozen=True)
class ParseTextEvalCase:
    case_id: str
    text: str
    language: str
    expected_lookup_name: str
    expected_quantity_g: float
    expected_source: str
    expected_calorie_range: tuple[float, float]
    expected_candidate_id: str | None = None
    ai_payload: dict[str, Any] = field(default_factory=dict)
    provider_candidates: list[dict[str, Any]] = field(default_factory=list)
    provider_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    local_reference: dict[str, Any] | None = None
    expected_items: tuple[dict[str, Any], ...] = ()
    expected_meal_macros: dict[str, Any] = field(default_factory=dict)
    expected_meal_micros: dict[str, dict[str, Any]] = field(default_factory=dict)
    reference_source_required: bool = True


@dataclass(frozen=True)
class ParseTextEvalObservation:
    response: Any
    extracted_lookup_name: str | None
    extracted_quantity_g: float | None
    selected_candidate_id: str | None
    provider_searches: int
    provider_details: int
    duration_ms: float
    extracted_items: tuple[dict[str, Any], ...] = ()
    selected_candidate_ids: tuple[str | None, ...] = ()
    evidence_kind: str = "offline_contract_only"
    final_output_kind: str = "offline_fixture_projection"
    generation_duration_ms: float | None = None
    input_tokens: float | None = None
    output_tokens: float | None = None
    cached_tokens: float | None = None
    model_route: str | None = None


@dataclass(frozen=True)
class ParseTextEvalCaseResult:
    case_id: str
    contract_pass: bool
    identity_pass: bool
    quantity_pass: bool
    candidate_pass: bool
    reference_pass: bool
    catastrophic_outlier: bool
    source: str | None
    provider_calls: int
    duration_ms: float
    reason_codes: tuple[str, ...] = ()
    provider_search_calls: int = 0
    provider_detail_calls: int = 0
    fallback_used: bool = False
    food_count: int = 0
    total_calories_kcal: float = 0.0
    calories_error_kcal: float | None = None
    portion_mae_g: float | None = None
    macro_mae_g: dict[str, float] = field(default_factory=dict)
    handler_food_count: int | None = None
    handler_total_calories_kcal: float | None = None
    handler_calories_error_kcal: float | None = None
    raw_model_food_count: int = 0
    raw_model_calories_error_kcal: float | None = None
    raw_model_macro_mae_g: dict[str, float] = field(default_factory=dict)
    raw_model_micro_coverage: dict[str, float] = field(default_factory=dict)
    raw_model_micro_normalized_mae: dict[str, float] = field(default_factory=dict)
    micro_coverage: dict[str, float] = field(default_factory=dict)
    micro_normalized_mae: dict[str, float] = field(default_factory=dict)
    handler_micro_coverage: dict[str, float] = field(default_factory=dict)
    handler_micro_normalized_mae: dict[str, float] = field(default_factory=dict)
    unknown_micro_population: dict[str, int] = field(default_factory=dict)
    evidence_kind: str = "offline_contract_only"
    final_output_kind: str = "offline_fixture_projection"
    generation_duration_ms: float | None = None
    input_tokens: float | None = None
    output_tokens: float | None = None
    cached_tokens: float | None = None
    model_route: str | None = None


@dataclass(frozen=True)
class ParseTextEvalSummary:
    case_count: int
    contract_pass_rate: float
    identity_quantity_pass_rate: float
    candidate_pass_rate: float
    common_reference_pass_rate: float
    catastrophic_outliers: int
    invalid_reference_accepts: int
    provider_calls: tuple[int, ...]
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    cases: tuple[ParseTextEvalCaseResult, ...]
    fallback_rate: float = 0.0
    provider_search_calls: tuple[int, ...] = ()
    provider_detail_calls: tuple[int, ...] = ()
    evidence_kind: str = "offline_contract_only"
    final_output_kinds: tuple[str, ...] = ()
    offline_fixture_latency_p50_ms: float | None = None
    offline_fixture_latency_p95_ms: float | None = None
    handler_end_to_end_latency_p50_ms: float | None = None
    handler_end_to_end_latency_p95_ms: float | None = None
    portion_mae_g: float | None = None
    calories_mae_kcal: float | None = None
    final_output_calories_mae_kcal: float | None = None
    macro_mae_g: dict[str, float] = field(default_factory=dict)
    final_output_macro_mae_g: dict[str, float] = field(default_factory=dict)
    micro_coverage: dict[str, float] = field(default_factory=dict)
    micro_normalized_mae: dict[str, float] = field(default_factory=dict)
    final_output_micro_coverage: dict[str, float] = field(default_factory=dict)
    final_output_micro_normalized_mae: dict[str, float] = field(default_factory=dict)
    handler_calories_mae_kcal: float | None = None
    handler_macro_mae_g: dict[str, float] = field(default_factory=dict)
    handler_micro_coverage: dict[str, float] = field(default_factory=dict)
    handler_micro_normalized_mae: dict[str, float] = field(default_factory=dict)
    raw_model_calories_mae_kcal: float | None = None
    raw_model_macro_mae_g: dict[str, float] = field(default_factory=dict)
    raw_model_micro_coverage: dict[str, float] = field(default_factory=dict)
    raw_model_micro_normalized_mae: dict[str, float] = field(default_factory=dict)
    unknown_micro_population: dict[str, int] = field(default_factory=dict)
    repeatability_calorie_sd_kcal: float | None = None
    generation_latency_p50_ms: float | None = None
    generation_latency_p95_ms: float | None = None
    provider_generation_latency_p50_ms: float | None = None
    provider_generation_latency_p95_ms: float | None = None
    input_tokens: float | None = None
    output_tokens: float | None = None
    cached_tokens: float | None = None
    model_routes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EvalRunner = Callable[[ParseTextEvalCase], Awaitable[ParseTextEvalObservation]]
