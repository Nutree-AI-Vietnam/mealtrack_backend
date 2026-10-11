from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MICRO_UNITS = {
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
MICRO_ERROR_FLOORS = {
    "vitamin_a": 1.0,
    "vitamin_c": 0.1,
    "vitamin_e": 0.1,
    "calcium": 0.1,
    "iron": 0.1,
    "magnesium": 0.1,
    "potassium": 0.1,
    "sodium": 0.1,
    "saturated_fat": 0.01,
    "added_sugar": 0.01,
}
MACRO_FIELDS = ("protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g")


@dataclass(frozen=True)
class PromptEvalObservation:
    """One candidate generation; telemetry stays unknown when the adapter omits it."""

    response_payload: dict[str, Any]
    duration_ms: float | None = None
    input_tokens: float | None = None
    output_tokens: float | None = None
    cached_tokens: float | None = None
    model_route: str | None = None
    evidence_kind: str = "offline_contract_only"


@dataclass(frozen=True)
class PromptEvalCase:
    case_id: str
    response_payload: dict[str, Any] = field(default_factory=dict)
    expected_foods: tuple[dict[str, Any], ...] = ()
    expected_calories_kcal: tuple[float, float] | None = None
    expected_macros: dict[str, float | tuple[float, float]] = field(
        default_factory=dict
    )
    expected_micros: dict[str, dict[str, Any]] = field(default_factory=dict)
    expected_is_food: bool | None = None


@dataclass(frozen=True)
class PromptEvalResult:
    name: str
    parse_success_rate: float
    validation_success_rate: float
    prompt_tokens_estimate: float
    score: float
    evidence_kind: str = "offline_contract_only"
    case_count: int = 0
    food_detection_accuracy: float | None = None
    identity_precision: float | None = None
    identity_recall: float | None = None
    portion_mae_g: float | None = None
    calories_mae_kcal: float | None = None
    macro_mae_g: dict[str, float] = field(default_factory=dict)
    micro_coverage: dict[str, float] = field(default_factory=dict)
    micro_normalized_mae: dict[str, float] = field(default_factory=dict)
    unknown_micro_population: dict[str, int] = field(default_factory=dict)
    repeatability_calorie_sd_kcal: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    input_tokens: float | None = None
    output_tokens: float | None = None
    cached_tokens: float | None = None
    model_routes: tuple[str, ...] = ()
