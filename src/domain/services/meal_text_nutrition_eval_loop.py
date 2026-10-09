"""Deterministic parse-text contract and reviewed-reference evaluator."""

from __future__ import annotations

from src.domain.evaluation.meal_text_nutrition_eval_aggregate import build_summary
from src.domain.evaluation.meal_text_nutrition_eval_models import (
    COMMON_REFERENCE_SOURCES,
    TEXT_MACROS,
    TEXT_MICRO_FLOORS,
    TEXT_MICRO_UNITS,
    EvalRunner,
    ParseTextEvalCase,
    ParseTextEvalCaseResult,
    ParseTextEvalObservation,
    ParseTextEvalSummary,
)
from src.domain.evaluation.meal_text_nutrition_eval_scoring import evaluate_case


class ParseTextNutritionEvalLoop:
    """Score every returned item and separate contract from provider evidence."""

    async def evaluate(
        self, cases: list[ParseTextEvalCase], runner: EvalRunner
    ) -> ParseTextEvalSummary:
        if not cases:
            raise ValueError("evaluation corpus must not be empty")
        observations = [await runner(case) for case in cases]
        results = [
            evaluate_case(case, observation)
            for case, observation in zip(cases, observations, strict=True)
        ]
        return build_summary(cases, observations, results)

    @staticmethod
    def enforce_gates(summary: ParseTextEvalSummary) -> None:
        gates = (
            (summary.contract_pass_rate == 1.0, "contract_pass_rate"),
            (summary.catastrophic_outliers == 0, "catastrophic_outliers"),
            (
                summary.identity_quantity_pass_rate >= 0.95,
                "identity_quantity_pass_rate",
            ),
            (summary.candidate_pass_rate >= 0.95, "candidate_pass_rate"),
            (summary.common_reference_pass_rate >= 0.90, "common_reference_pass_rate"),
            (summary.invalid_reference_accepts == 0, "invalid_reference_accepts"),
        )
        failures = [name for passed, name in gates if not passed]
        if failures:
            raise ValueError(
                "parse-text evaluation gates failed: " + ", ".join(failures)
            )


__all__ = [
    "COMMON_REFERENCE_SOURCES",
    "TEXT_MACROS",
    "TEXT_MICRO_FLOORS",
    "TEXT_MICRO_UNITS",
    "ParseTextEvalCase",
    "ParseTextEvalCaseResult",
    "ParseTextEvalObservation",
    "ParseTextEvalSummary",
    "ParseTextNutritionEvalLoop",
]
