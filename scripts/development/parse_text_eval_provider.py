"""Provider-only raw-model evaluator; performs no reference lookup or writes."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from scripts.development.parse_text_eval_budget import (
    LIVE_MAX_PROVIDER_GENERATIONS,
    LIVE_TIMEOUT_SECONDS,
    select_provider_batch,
)
from scripts.development.parse_text_eval_prompt_bundle import _prompt_for_language

from src.domain.model.ai.nutrition_contracts import MealTextNutritionResponse
from src.domain.model.nutrition.macros import Macros
from src.domain.model.nutrition.micros import Micros
from src.domain.services.meal_text_nutrition_eval_loop import (
    ParseTextEvalCase,
    ParseTextEvalObservation,
    ParseTextNutritionEvalLoop,
)
from src.domain.services.nutrition_resolver import normalize_food_lookup_name
from src.domain.services.prompts.input_sanitizer import sanitize_user_description


async def _run_provider_only_case(
    case: ParseTextEvalCase,
    prompt: str,
    service: Any,
) -> ParseTextEvalObservation:
    """Call only configured AI generation: no handler, lookup, provider search, or write."""
    started = time.perf_counter()
    from src.infra.adapters.meal_generation_service import MealGenerationService

    generation_service = service or MealGenerationService()
    user_prompt = f"language: {case.language or 'en'}\nmeal: {sanitize_user_description(case.text)}"
    generated = await generation_service.generate_meal_plan_async(
        prompt=user_prompt,
        system_message=prompt,
        response_type="json",
        max_tokens=2048,
        schema=MealTextNutritionResponse,
        model_purpose="parse_text",
        thinking_budget=0,
    )
    validated = MealTextNutritionResponse.model_validate(generated)
    extracted: list[dict[str, Any]] = []
    response_items: list[Any] = []
    for item in validated.items:
        raw_item = item.model_dump(exclude_none=False)
        lookup_name = normalize_food_lookup_name(str(item.lookup_name or item.name))
        micros = item.micros.model_dump(exclude_none=True) if item.micros else None
        macros = item.macros.model_dump()
        extracted.append(
            {
                **raw_item,
                "lookup_name": lookup_name,
                "quantity_g": item.quantity_g,
                "macros": macros,
                "micros": micros,
            }
        )
        response_items.append(
            SimpleNamespace(
                name=item.name,
                canonical_name=lookup_name,
                quantity=item.quantity_g or item.quantity,
                unit=item.unit,
                calories=Macros.raw_total_calories(
                    item.macros.protein_g,
                    item.macros.carbs_g,
                    item.macros.fat_g,
                    item.macros.fiber_g,
                ),
                protein=item.macros.protein_g,
                carbs=item.macros.carbs_g,
                fat=item.macros.fat_g,
                fiber=item.macros.fiber_g,
                sugar=item.macros.sugar_g,
                micros=(
                    Micros.from_dict(item.micros.model_dump(exclude_none=True))
                    if item.micros
                    else None
                ),
                data_source="ai_estimate",
            )
        )
    response = SimpleNamespace(
        items=response_items,
        total_calories=sum(item.calories for item in response_items),
    )
    duration = (time.perf_counter() - started) * 1000
    return ParseTextEvalObservation(
        response=response,
        extracted_lookup_name=extracted[0]["lookup_name"] if extracted else None,
        extracted_quantity_g=extracted[0].get("quantity_g") if extracted else None,
        selected_candidate_id=None,
        provider_searches=0,
        provider_details=0,
        duration_ms=duration,
        extracted_items=tuple(extracted),
        evidence_kind="provider_observation",
        final_output_kind="raw_model_contract_projection",
        generation_duration_ms=duration,
        # AIModelManager currently does not expose usage or physical attempts here.
        input_tokens=None,
        output_tokens=None,
        cached_tokens=None,
        model_route=None,
    )


async def run_provider_pair(
    cases: list[ParseTextEvalCase],
    baseline: dict[str, str],
    candidate: dict[str, str],
    repetitions: int,
    max_cases: int,
    case_offset: int = 0,
) -> dict[str, Any]:
    admitted, selected = select_provider_batch(
        cases, repetitions, max_cases, case_offset
    )
    for case in selected:
        _prompt_for_language(baseline, case.language)
        _prompt_for_language(candidate, case.language)
    from src.infra.adapters.meal_generation_service import MealGenerationService

    service = MealGenerationService()
    outputs: dict[str, list[tuple[ParseTextEvalCase, ParseTextEvalObservation]]] = {
        "baseline": [],
        "candidate": [],
    }
    deadline = time.monotonic() + LIVE_TIMEOUT_SECONDS
    generation_count = 0
    for repetition in range(repetitions):
        for case_index, case in enumerate(selected):
            order = (
                ("candidate", "baseline")
                if (case_index + repetition) % 2
                else ("baseline", "candidate")
            )
            for name in order:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "provider-only evaluation exceeded its run deadline"
                    )
                bundle = baseline if name == "baseline" else candidate
                observation = await asyncio.wait_for(
                    _run_provider_only_case(
                        case, _prompt_for_language(bundle, case.language), service
                    ),
                    timeout=remaining,
                )
                outputs[name].append((case, observation))
                generation_count += 1
                if generation_count > LIVE_MAX_PROVIDER_GENERATIONS:
                    raise RuntimeError("provider generation cap exceeded")
    summaries: dict[str, dict[str, Any]] = {}
    loop = ParseTextNutritionEvalLoop()
    for name in ("baseline", "candidate"):
        pairs = outputs[name]
        iterator = iter(pairs)

        async def replay(_case: ParseTextEvalCase, *, _iterator=iterator):
            return next(_iterator)[1]

        summary = await loop.evaluate([case for case, _ in pairs], replay)
        summaries[name] = summary.to_dict()
    return {
        "evidence_kind": "provider_only_raw_model",
        "case_count": admitted,
        "case_offset": case_offset,
        "repetitions": repetitions,
        "provider_generations": generation_count,
        "application_generation_cap": LIVE_MAX_PROVIDER_GENERATIONS,
        "reference_searches": 0,
        "reference_details": 0,
        "persistence_writes": 0,
        "physical_attempt_count": None,
        "results": summaries,
    }
