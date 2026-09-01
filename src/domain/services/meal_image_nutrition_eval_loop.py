"""Deterministic quality gates and evaluation loop for meal image nutrition scans."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any


@dataclass(frozen=True)
class MealImageEvalCase:
    case_id: str
    language: str
    category: str
    expected_is_food: bool
    expected_dish_name: str
    expected_foods: list[dict[str, Any]]
    expected_calorie_range: tuple[float, float]
    ai_payload: dict[str, Any] = field(default_factory=dict)
    local_reference: dict[str, Any] | None = None
    description: str = ""


@dataclass(frozen=True)
class MealImageEvalObservation:
    response: Any
    is_food: bool
    dish_name: str | None
    foods: list[dict[str, Any]]
    total_calories: float
    duration_ms: float
    provider_calls: int = 1


@dataclass(frozen=True)
class MealImageEvalCaseResult:
    case_id: str
    contract_pass: bool
    food_presence_pass: bool
    dish_name_pass: bool
    ingredient_f1: float
    quantity_ape: float
    macro_wape: float
    catastrophic_outlier: bool
    duration_ms: float
    provider_calls: int
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MealImageEvalSummary:
    case_count: int
    contract_pass_rate: float
    food_presence_accuracy: float
    mean_ingredient_f1: float
    quantity_mape: float
    macro_wape: float
    catastrophic_outliers: int
    provider_calls: tuple[int, ...]
    latency_p50_ms: float
    latency_p95_ms: float
    cases: tuple[MealImageEvalCaseResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


EvalRunner = Callable[[MealImageEvalCase], Awaitable[MealImageEvalObservation]]


class MealImageNutritionEvalLoop:
    """Evaluate meal image analysis candidates against deterministic golden cases."""

    async def evaluate(
        self, cases: list[MealImageEvalCase], runner: EvalRunner
    ) -> MealImageEvalSummary:
        if not cases:
            raise ValueError("evaluation corpus must not be empty")

        results = [self._evaluate_case(case, await runner(case)) for case in cases]
        latencies = [r.duration_ms for r in results]

        return MealImageEvalSummary(
            case_count=len(results),
            contract_pass_rate=_rate(results, "contract_pass"),
            food_presence_accuracy=_rate(results, "food_presence_pass"),
            mean_ingredient_f1=round(
                sum(r.ingredient_f1 for r in results) / len(results), 4
            ),
            quantity_mape=round(sum(r.quantity_ape for r in results) / len(results), 4),
            macro_wape=round(sum(r.macro_wape for r in results) / len(results), 4),
            catastrophic_outliers=sum(r.catastrophic_outlier for r in results),
            provider_calls=tuple(r.provider_calls for r in results),
            latency_p50_ms=round(median(latencies), 3),
            latency_p95_ms=round(_percentile(latencies, 0.95), 3),
            cases=tuple(results),
        )

    def _evaluate_case(
        self, case: MealImageEvalCase, obs: MealImageEvalObservation
    ) -> MealImageEvalCaseResult:
        reasons: list[str] = []

        contract_pass = (
            obs.response is not None
            and isinstance(obs.is_food, bool)
            and isinstance(obs.foods, list)
            and isinstance(obs.total_calories, (int, float))
        )
        if not contract_pass:
            reasons.append("contract_schema_invalid")

        food_presence_pass = obs.is_food == case.expected_is_food
        if not food_presence_pass:
            reasons.append("food_presence_mismatch")

        dish_name_pass = True
        if case.expected_is_food and case.expected_dish_name:
            dish_name_pass = bool(
                obs.dish_name
                and (
                    case.expected_dish_name.casefold() in obs.dish_name.casefold()
                    or obs.dish_name.casefold() in case.expected_dish_name.casefold()
                )
            )
            if not dish_name_pass:
                reasons.append("dish_name_mismatch")

        ingredient_f1 = 1.0
        quantity_ape = 0.0
        macro_wape = 0.0
        catastrophic = False

        if not case.expected_is_food:
            if obs.is_food:
                catastrophic = True
                reasons.append("non_food_detected_as_food")
        else:
            ingredient_f1 = _calculate_ingredient_f1(case.expected_foods, obs.foods)
            quantity_ape = _calculate_quantity_ape(case.expected_foods, obs.foods)
            macro_wape = _calculate_macro_wape(case.expected_foods, obs.foods)

            min_cal, max_cal = case.expected_calorie_range
            if obs.total_calories < min_cal * 0.5 or obs.total_calories > max_cal * 2.0:
                catastrophic = True
                reasons.append("catastrophic_calorie_outlier")

        return MealImageEvalCaseResult(
            case_id=case.case_id,
            contract_pass=contract_pass,
            food_presence_pass=food_presence_pass,
            dish_name_pass=dish_name_pass,
            ingredient_f1=round(ingredient_f1, 4),
            quantity_ape=round(quantity_ape, 4),
            macro_wape=round(macro_wape, 4),
            catastrophic_outlier=catastrophic,
            duration_ms=round(obs.duration_ms, 3),
            provider_calls=obs.provider_calls,
            reason_codes=tuple(reasons),
        )

    @staticmethod
    def enforce_gates(summary: MealImageEvalSummary) -> None:
        failures: list[str] = []
        gates = (
            (summary.contract_pass_rate == 1.0, "contract_pass_rate"),
            (summary.catastrophic_outliers == 0, "catastrophic_outliers"),
            (summary.food_presence_accuracy >= 0.95, "food_presence_accuracy"),
            (summary.mean_ingredient_f1 >= 0.85, "mean_ingredient_f1"),
            (summary.quantity_mape <= 0.20, "quantity_mape"),
            (summary.macro_wape <= 0.25, "macro_wape"),
        )
        for passed, name in gates:
            if not passed:
                failures.append(name)
        if failures:
            raise AssertionError(
                f"Meal image quality gates failed: {', '.join(failures)}"
            )


def _rate(results: list[MealImageEvalCaseResult], attr: str) -> float:
    if not results:
        return 0.0
    return round(sum(bool(getattr(r, attr)) for r in results) / len(results), 4)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(math.ceil(pct * len(sorted_vals))) - 1
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


def _calculate_ingredient_f1(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> float:
    if not expected and not actual:
        return 1.0
    if not expected or not actual:
        return 0.0

    exp_names = {f["name"].casefold().strip() for f in expected if f.get("name")}
    act_names = {f["name"].casefold().strip() for f in actual if f.get("name")}

    if not exp_names and not act_names:
        return 1.0

    tp = len(exp_names & act_names)
    precision = tp / len(act_names) if act_names else 0.0
    recall = tp / len(exp_names) if exp_names else 0.0

    if precision + recall == 0:
        return 0.0
    return 2 * (precision * recall) / (precision + recall)


def _calculate_quantity_ape(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> float:
    total_exp_q = sum(float(f.get("quantity_g", 0)) for f in expected)
    total_act_q = sum(float(f.get("quantity_g", 0)) for f in actual)

    if total_exp_q <= 0:
        return 0.0 if total_act_q <= 0 else 1.0

    return abs(total_act_q - total_exp_q) / total_exp_q


def _calculate_macro_wape(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> float:
    exp_p = sum(float(f.get("macros", {}).get("protein", 0)) for f in expected)
    exp_c = sum(float(f.get("macros", {}).get("carbs", 0)) for f in expected)
    exp_f = sum(float(f.get("macros", {}).get("fat", 0)) for f in expected)
    exp_total = exp_p + exp_c + exp_f

    if exp_total <= 0:
        return 0.0

    act_p = sum(float(f.get("macros", {}).get("protein", 0)) for f in actual)
    act_c = sum(float(f.get("macros", {}).get("carbs", 0)) for f in actual)
    act_f = sum(float(f.get("macros", {}).get("fat", 0)) for f in actual)

    err = abs(act_p - exp_p) + abs(act_c - exp_c) + abs(act_f - exp_f)
    return err / exp_total
