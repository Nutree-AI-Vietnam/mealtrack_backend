from __future__ import annotations

from typing import Any

from src.domain.evaluation.prompt_eval_models import (
    MACRO_FIELDS,
    MICRO_ERROR_FLOORS,
    MICRO_UNITS,
    PromptEvalCase,
    PromptEvalObservation,
    PromptEvalResult,
)
from src.domain.evaluation.prompt_eval_scoring import score_candidate
from src.domain.parsers.vision_response_parser import VisionResponseParser


class PromptEvalLoop:
    """Evaluate prompt candidates while separating contract and provider evidence."""

    def __init__(self, parser: VisionResponseParser | None = None):
        self._parser = parser or VisionResponseParser()

    def rank_candidates(
        self,
        candidates: dict[str, str],
        cases: list[PromptEvalCase],
        case_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> list[PromptEvalResult]:
        if not cases:
            raise ValueError("cases must not be empty")
        overrides = case_overrides or {}
        ranked = [
            score_candidate(
                self._parser,
                name,
                prompt,
                cases,
                overrides.get(name, {}),
            )
            for name, prompt in candidates.items()
        ]
        return sorted(
            ranked,
            key=lambda item: (item.parse_success_rate, -item.prompt_tokens_estimate),
            reverse=True,
        )

    def enforce_thresholds(
        self,
        result: PromptEvalResult,
        min_parse_success_rate: float,
        max_prompt_tokens: float,
        min_validation_success_rate: float = 0.0,
    ) -> None:
        failures: list[str] = []
        if result.parse_success_rate < min_parse_success_rate:
            failures.append(
                f"parse_success_rate={result.parse_success_rate:.3f} < {min_parse_success_rate:.3f}"
            )
        if result.validation_success_rate < min_validation_success_rate:
            failures.append(
                f"validation_success_rate={result.validation_success_rate:.3f} < {min_validation_success_rate:.3f}"
            )
        if result.prompt_tokens_estimate > max_prompt_tokens:
            failures.append(
                f"prompt_tokens_estimate={result.prompt_tokens_estimate:.1f} > {max_prompt_tokens:.1f}"
            )
        if failures:
            raise ValueError("; ".join(failures))


__all__ = [
    "MACRO_FIELDS",
    "MICRO_ERROR_FLOORS",
    "MICRO_UNITS",
    "PromptEvalCase",
    "PromptEvalLoop",
    "PromptEvalObservation",
    "PromptEvalResult",
]
