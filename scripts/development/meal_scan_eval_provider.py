"""Bounded provider runner for reviewed meal-photo prompt comparisons."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from scripts.development.meal_scan_eval_manifest import _corpus_status
from scripts.development.meal_scan_eval_prompt_bundle import (
    MAX_PROVIDER_CASES,
    MAX_PROVIDER_GENERATIONS,
    PROVIDER_TIMEOUT_SECONDS,
    ReviewedMealCase,
    _ExplicitPromptStrategy,
    prompt_for_language,
)

from src.domain.model.ai.nutrition_contracts import VisionNutritionResponse
from src.domain.services.meal_analysis.prompt_eval_loop import PromptEvalLoop
from src.infra.adapters.vision_ai_service import VisionAIService


async def run_provider_pair(
    cases: list[ReviewedMealCase],
    baseline: dict[str, str],
    candidate: dict[str, str],
    repetitions: int,
    max_cases: int,
    case_offset: int = 0,
) -> dict[str, Any]:
    admitted, selected = select_provider_batch(
        cases, repetitions, max_cases, case_offset
    )
    service = VisionAIService()
    outputs: dict[str, dict[str, list[dict[str, Any]]]] = {
        "baseline": {},
        "candidate": {},
    }
    started = time.monotonic()
    deadline = started + PROVIDER_TIMEOUT_SECONDS
    generation_count = 0
    for repetition in range(repetitions):
        for case_index, reviewed in enumerate(selected):
            order = (
                ("candidate", "baseline")
                if (case_index + repetition) % 2
                else ("baseline", "candidate")
            )
            for name in order:
                prompt = prompt_for_language(
                    baseline if name == "baseline" else candidate, reviewed.language
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "provider evaluation exceeded its 300-second run deadline"
                    )
                image_bytes = reviewed.image_path.read_bytes()
                call_started = time.perf_counter()
                result = await asyncio.wait_for(
                    service.analyze_with_strategy(
                        image_bytes,
                        _ExplicitPromptStrategy(prompt, reviewed.language, name),
                    ),
                    timeout=remaining,
                )
                duration = (time.perf_counter() - call_started) * 1000
                VisionNutritionResponse.model_validate(
                    result.get("structured_data", {})
                )
                outputs[name].setdefault(reviewed.evaluation.case_id, []).append(
                    {
                        "response_payload": result,
                        "duration_ms": duration,
                        "evidence_kind": "provider_observation",
                    }
                )
                generation_count += 1
                if generation_count > MAX_PROVIDER_GENERATIONS:
                    raise RuntimeError("provider generation cap exceeded")
    loop = PromptEvalLoop()
    prompt_text = {
        "baseline": "\n".join(baseline.values()),
        "candidate": "\n".join(candidate.values()),
    }
    scored = {
        name: [case.evaluation for case in selected]
        for name in ("baseline", "candidate")
    }
    results = {
        name: loop.rank_candidates(
            {name: prompt_text[name]},
            scored[name],
            case_overrides={name: outputs[name]},
        )[0].__dict__
        for name in ("baseline", "candidate")
    }
    return {
        "evidence_kind": "provider_observation",
        "case_count": admitted,
        "corpus_case_count": len(cases),
        "case_offset": case_offset,
        "repetitions": repetitions,
        "provider_generations": generation_count,
        "generation_cap": MAX_PROVIDER_GENERATIONS,
        "case_splits": {
            "development": sum(item.split == "development" for item in selected),
            "held_out": sum(item.split == "held_out" for item in selected),
        },
        "quality_evaluation_status": _corpus_status(cases),
        "results": results,
    }


def select_provider_batch(
    cases: list[ReviewedMealCase],
    repetitions: int,
    max_cases: int,
    case_offset: int = 0,
) -> tuple[int, list[ReviewedMealCase]]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least one")
    if case_offset < 0:
        raise ValueError("case offset must be non-negative")
    remaining = cases[case_offset:]
    admitted = min(
        max_cases,
        MAX_PROVIDER_CASES,
        MAX_PROVIDER_GENERATIONS // (2 * repetitions),
        len(remaining),
    )
    if admitted < 1:
        raise ValueError("provider generation budget admits no paired cases")
    return admitted, remaining[:admitted]
