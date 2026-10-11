"""Hard limits and safe batch selection for provider-only parse-text evaluation."""

from __future__ import annotations

from src.domain.services.meal_text_nutrition_eval_loop import ParseTextEvalCase

LIVE_MAX_CASES = 25
LIVE_MAX_PROVIDER_GENERATIONS = 50
LIVE_TIMEOUT_SECONDS = 300.0


def select_provider_batch(
    cases: list[ParseTextEvalCase],
    repetitions: int,
    max_cases: int,
    case_offset: int = 0,
) -> tuple[int, list[ParseTextEvalCase]]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    if case_offset < 0:
        raise ValueError("case offset must be non-negative")
    remaining = cases[case_offset:]
    admitted = min(
        max_cases,
        LIVE_MAX_CASES,
        LIVE_MAX_PROVIDER_GENERATIONS // (2 * repetitions),
        len(remaining),
    )
    if admitted < 1:
        raise ValueError("provider generation budget admits no paired cases")
    return admitted, remaining[:admitted]
