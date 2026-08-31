"""Deterministic quality gates and evaluation loop for food label OCR and nutrition extraction."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any


@dataclass(frozen=True)
class FoodLabelEvalCase:
    case_id: str
    language: str
    format_type: str
    expected_is_food_label: bool
    expected_product_name: str
    expected_serving_grams: float
    expected_servings_per_package: float
    expected_calories_per_serving: float
    expected_macros: dict[str, float]
    ai_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FoodLabelEvalObservation:
    response: Any
    is_food_label: bool
    product_name: str | None
    serving_grams: float | None
    servings_per_package: float | None
    calories_per_serving: float | None
    macros: dict[str, float]
    duration_ms: float
    provider_calls: int = 1
    persisted_meal: bool = False


@dataclass(frozen=True)
class FoodLabelEvalCaseResult:
    case_id: str
    contract_pass: bool
    label_presence_pass: bool
    product_name_pass: bool
    serving_grams_pass: bool
    servings_per_package_pass: bool
    calorie_accuracy_pass: bool
    macro_pass: bool
    label_consistency_pass: bool
    non_label_persisted: bool
    catastrophic_outlier: bool
    duration_ms: float
    provider_calls: int
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FoodLabelEvalSummary:
    case_count: int
    contract_pass_rate: float
    label_presence_accuracy: float
    field_match_rate: float
    calorie_accuracy_rate: float
    label_consistency_rate: float
    non_label_persisted_count: int
    catastrophic_outliers: int
    provider_calls: tuple[int, ...]
    latency_p50_ms: float
    latency_p95_ms: float
    cases: tuple[FoodLabelEvalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EvalRunner = Callable[[FoodLabelEvalCase], Awaitable[FoodLabelEvalObservation]]


class FoodLabelEvalLoop:
    """Evaluate food label extraction candidates against deterministic golden cases."""

    async def evaluate(
        self, cases: list[FoodLabelEvalCase], runner: EvalRunner
    ) -> FoodLabelEvalSummary:
        if not cases:
            raise ValueError("evaluation corpus must not be empty")

        results = [self._evaluate_case(case, await runner(case)) for case in cases]
        latencies = [r.duration_ms for r in results]

        field_match_scores = [
            (
                r.product_name_pass
                + r.serving_grams_pass
                + r.servings_per_package_pass
                + r.calorie_accuracy_pass
                + r.macro_pass
            )
            / 5.0
            for r in results
            if r.label_presence_pass
        ]
        mean_field_match = (
            round(sum(field_match_scores) / len(field_match_scores), 4)
            if field_match_scores
            else 0.0
        )

        return FoodLabelEvalSummary(
            case_count=len(results),
            contract_pass_rate=_rate(results, "contract_pass"),
            label_presence_accuracy=_rate(results, "label_presence_pass"),
            field_match_rate=mean_field_match,
            calorie_accuracy_rate=_rate(results, "calorie_accuracy_pass"),
            label_consistency_rate=_rate(results, "label_consistency_pass"),
            non_label_persisted_count=sum(r.non_label_persisted for r in results),
            catastrophic_outliers=sum(r.catastrophic_outlier for r in results),
            provider_calls=tuple(r.provider_calls for r in results),
            latency_p50_ms=round(median(latencies), 3),
            latency_p95_ms=round(_percentile(latencies, 0.95), 3),
            cases=tuple(results),
        )

    def _evaluate_case(
        self, case: FoodLabelEvalCase, obs: FoodLabelEvalObservation
    ) -> FoodLabelEvalCaseResult:
        reasons: list[str] = []

        contract_pass = (
            obs.response is not None
            and isinstance(obs.is_food_label, bool)
            and isinstance(obs.macros, dict)
        )
        if not contract_pass:
            reasons.append("contract_schema_invalid")

        label_presence_pass = obs.is_food_label == case.expected_is_food_label
        if not label_presence_pass:
            reasons.append("label_presence_mismatch")

        product_name_pass = True
        serving_grams_pass = True
        servings_per_package_pass = True
        calorie_accuracy_pass = True
        macro_pass = True
        label_consistency_pass = True
        catastrophic = False
        non_label_persisted = False

        if not case.expected_is_food_label:
            if obs.is_food_label:
                catastrophic = True
                reasons.append("non_label_detected_as_label")
            if obs.persisted_meal:
                non_label_persisted = True
                catastrophic = True
                reasons.append("non_label_persisted_as_meal")
        else:
            if case.expected_product_name:
                product_name_pass = bool(
                    obs.product_name
                    and (
                        case.expected_product_name.casefold()
                        in obs.product_name.casefold()
                        or obs.product_name.casefold()
                        in case.expected_product_name.casefold()
                    )
                )
                if not product_name_pass:
                    reasons.append("product_name_mismatch")

            if case.expected_serving_grams > 0:
                act_g = obs.serving_grams or 0.0
                serving_grams_pass = (
                    abs(act_g - case.expected_serving_grams)
                    / case.expected_serving_grams
                    <= 0.10
                )
                if not serving_grams_pass:
                    reasons.append("serving_grams_mismatch")

            if case.expected_servings_per_package > 0:
                act_s = obs.servings_per_package or 1.0
                servings_per_package_pass = (
                    abs(act_s - case.expected_servings_per_package) <= 0.5
                )
                if not servings_per_package_pass:
                    reasons.append("servings_per_package_mismatch")

            exp_cal = case.expected_calories_per_serving
            act_cal = obs.calories_per_serving
            if exp_cal is not None and act_cal is not None:
                cal_err = (
                    abs(act_cal - exp_cal) / max(exp_cal, 1.0)
                    if exp_cal > 0
                    else abs(act_cal)
                )
                calorie_accuracy_pass = cal_err <= 0.10
                if not calorie_accuracy_pass:
                    reasons.append("calorie_accuracy_mismatch")
                if act_cal > max(exp_cal * 3.0, 5000.0) or act_cal < 0:
                    catastrophic = True
                    reasons.append("catastrophic_calorie_value")
            elif exp_cal is not None and act_cal is None:
                calorie_accuracy_pass = False
                reasons.append("missing_printed_calories")

            # Check macro accuracy
            exp_p = float(case.expected_macros.get("protein", 0))
            exp_c = float(case.expected_macros.get("carbs", 0))
            exp_f = float(case.expected_macros.get("fat", 0))
            act_p = float(obs.macros.get("protein", 0))
            act_c = float(obs.macros.get("carbs", 0))
            act_f = float(obs.macros.get("fat", 0))

            macro_diff = abs(act_p - exp_p) + abs(act_c - exp_c) + abs(act_f - exp_f)
            exp_macro_sum = exp_p + exp_c + exp_f
            if exp_macro_sum > 0 and (macro_diff / exp_macro_sum) > 0.20:
                macro_pass = False
                reasons.append("macro_extraction_error")

            # Check internal consistency (Atwater 4-4-9 macro sum with label-rounding tolerance)
            if act_cal is not None and exp_macro_sum > 0:
                derived_cal = act_p * 4.0 + act_c * 4.0 + act_f * 9.0
                # Tolerance allows standard FDA/EU rounding rules (up to 20% or 30 kcal)
                diff = abs(act_cal - derived_cal)
                if diff > max(30.0, derived_cal * 0.30):
                    label_consistency_pass = False
                    reasons.append("label_rounding_inconsistent")

        return FoodLabelEvalCaseResult(
            case_id=case.case_id,
            contract_pass=contract_pass,
            label_presence_pass=label_presence_pass,
            product_name_pass=product_name_pass,
            serving_grams_pass=serving_grams_pass,
            servings_per_package_pass=servings_per_package_pass,
            calorie_accuracy_pass=calorie_accuracy_pass,
            macro_pass=macro_pass,
            label_consistency_pass=label_consistency_pass,
            non_label_persisted=non_label_persisted,
            catastrophic_outlier=catastrophic,
            duration_ms=round(obs.duration_ms, 3),
            provider_calls=obs.provider_calls,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def enforce_gates(summary: FoodLabelEvalSummary) -> None:
        failures: list[str] = []
        gates = (
            (summary.contract_pass_rate == 1.0, "contract_pass_rate"),
            (summary.catastrophic_outliers == 0, "catastrophic_outliers"),
            (summary.non_label_persisted_count == 0, "non_label_persisted_count"),
            (summary.label_presence_accuracy >= 0.95, "label_presence_accuracy"),
            (summary.field_match_rate >= 0.90, "field_match_rate"),
            (summary.calorie_accuracy_rate >= 0.95, "calorie_accuracy_rate"),
        )
        for passed, name in gates:
            if not passed:
                failures.append(name)
        if failures:
            raise AssertionError(
                f"Food label quality gates failed: {', '.join(failures)}"
            )


def _rate(results: list[FoodLabelEvalCaseResult], attr: str) -> float:
    if not results:
        return 0.0
    return round(sum(bool(getattr(r, attr)) for r in results) / len(results), 4)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(math.ceil(pct * len(sorted_vals))) - 1
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]
