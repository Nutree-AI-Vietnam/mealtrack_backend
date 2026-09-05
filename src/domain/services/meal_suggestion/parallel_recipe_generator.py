"""
Parallel recipe generation for meal suggestions.
Implements 3-phase: name generation → parallel recipe generation → translation.
Per-recipe attempt logic lives in recipe_attempt_builder.py.
"""

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import replace
from typing import List, Optional

from src.domain.model.meal_suggestion import MealSuggestion, SuggestionSession
from src.domain.model.meal_suggestion.suggestion_translation_result import (
    SuggestionTranslationResult,
)
from src.domain.model.translation_result import TranslationOutcome
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.meal_suggestion.discovery_fallback_builder import (
    build_discovery_fallback_meals,
)
from src.domain.services.meal_suggestion.macro_validation_service import (
    MacroValidationService,
)
from src.domain.services.meal_suggestion.nutrition_lookup_service import (
    NutritionLookupService,
)
from src.domain.services.meal_suggestion.recipe_attempt_builder import (
    SELECTED_RECIPE_ATTEMPT_TIMEOUT,
    attempt_recipe_generation,
    fallback_selected_recipe,
)
from src.domain.services.meal_suggestion.suggestion_translation_service import (
    SuggestionTranslationService,
)
from src.domain.services.prompts.system_prompts import SystemPrompts

logger = logging.getLogger(__name__)

LANGUAGE_NAMES = {
    "en": "English",
    "vi": "Vietnamese",
    "fr": "French",
    "es": "Spanish",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "ru": "Russian",
    "pt": "Portuguese",
    "hi": "Hindi",
    "ar": "Arabic",
    "tr": "Turkish",
    "nl": "Dutch",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "no": "Norwegian",
    "cs": "Czech",
    "el": "Greek",
    "he": "Hebrew",
    "id": "Indonesian",
    "ms": "Malay",
    "th": "Thai",
}


def get_language_name(code: str) -> str:
    """Map language code to full English name for prompt instructions."""
    return LANGUAGE_NAMES.get(code.lower(), "English")


class ParallelRecipeGenerator:
    """
    3-phase parallel meal generation:
      Phase 1 — 4 diverse meal names (1 AI call, ~1-2s)
      Phase 2 — 4 recipes in parallel, take first 3 successes (~6-8s)
      Phase 3 — Translate to target language if non-English (~2-3s)
    """

    DEFAULT_SUGGESTIONS_COUNT = 3
    MIN_ACCEPTABLE_RESULTS = 1  # was 2 — allow partial results
    PHASE1_TIMEOUT = 20
    DISCOVERY_TIMEOUT = 15

    def __init__(
        self,
        generation_service: MealGenerationServicePort,
        translation_service: SuggestionTranslationService,
        macro_validator: MacroValidationService,
        nutrition_lookup: NutritionLookupService,
        meal_names_schema_class: type,
        discovery_meals_schema_class: type,
        recipe_details_schema_class: type | None = None,
    ) -> None:
        self._generation = generation_service
        self._translation_service = translation_service
        self._macro_validator = macro_validator
        self._nutrition_lookup = nutrition_lookup
        self._meal_names_schema = meal_names_schema_class
        self._discovery_meals_schema = discovery_meals_schema_class
        self._recipe_details_schema = recipe_details_schema_class

    async def generate(
        self,
        session: SuggestionSession,
        exclude_meal_names: List[str],
        suggestion_count: Optional[int] = None,
    ) -> List[MealSuggestion]:
        """Run 3-phase generation. Raises RuntimeError if < MIN_ACCEPTABLE_RESULTS succeed."""
        count = suggestion_count or self.DEFAULT_SUGGESTIONS_COUNT
        start_time = time.time()
        target_lang = get_language_name(session.language)

        meal_names = await self._phase1_generate_names(
            session, exclude_meal_names, target_lang, count
        )
        phase1_elapsed = time.time() - start_time

        if session.language and session.language != "en":
            suggestions = await self._phase2_and_translate(
                session, meal_names, target_lang, count
            )
            phase2_elapsed = time.time() - start_time - phase1_elapsed
            phase3_elapsed = 0.0  # pipelined — no separate phase
        else:
            suggestions = await self._phase2_generate_recipes(
                session, meal_names, target_lang, count, preserve_order=False
            )
            phase2_elapsed = time.time() - start_time - phase1_elapsed
            phase3_elapsed = 0.0

        total_elapsed = time.time() - start_time
        logger.debug(
            f"[PIPELINE-COMPLETE] session={session.id} | total_elapsed={total_elapsed:.2f}s | "
            f"phase1={phase1_elapsed:.2f}s | phase2={phase2_elapsed:.2f}s | "
            f"phase3={phase3_elapsed:.2f}s | language={session.language} | "
            f"returned={len(suggestions)}"
        )
        return suggestions

    async def generate_discovery(
        self,
        session: SuggestionSession,
        exclude_meal_names: List[str],
        count: int = 6,
    ) -> List[dict]:
        """Lightweight discovery: single AI call → list of {name, calories, P, C, F}.

        Returns list of dicts with validated macros. ~200 tokens total.
        """
        from src.domain.services.prompts.prompt_template_manager import (
            PromptTemplateManager,
        )

        discovery_schema = self._discovery_meals_schema
        if not discovery_schema:
            return self._build_discovery_fallback(
                session,
                exclude_meal_names,
                count,
                "missing discovery schema",
            )

        start = time.time()
        # Request extra for dedup headroom
        request_count = count + 2

        system = SystemPrompts.DISCOVERY_SYSTEM.format(count=request_count)
        prompt = PromptTemplateManager.build_discovery_prompt(
            meal_type=session.meal_type,
            target_calories=session.target_calories,
            count=request_count,
            ingredients=session.ingredients[:4] if session.ingredients else [],
            cuisine_region=getattr(session, "cuisine_region", None),
            exclude_meal_names=exclude_meal_names,
            protein_target=getattr(session, "protein_target", None),
            carbs_target=getattr(session, "carbs_target", None),
            fat_target=getattr(session, "fat_target", None),
        )

        try:
            raw = await asyncio.wait_for(
                self._generation.generate_meal_plan_async(
                    prompt,
                    system,
                    "json",
                    1000,
                    discovery_schema,
                    "discovery",
                ),
                timeout=self.DISCOVERY_TIMEOUT,
            )
        except Exception as e:
            attempted_models = getattr(e, "attempted_models", None)
            logger.warning(
                "[DISCOVERY-FALLBACK] session=%s | error_type=%s | attempted_models=%s",
                session.id,
                type(e).__name__,
                attempted_models,
            )
            return self._build_discovery_fallback(
                session,
                exclude_meal_names,
                count,
                type(e).__name__,
            )

        # Dedup + validate macros
        seen: set = set()
        results: list = []
        raw_meals = raw.get("meals", [])
        for meal in raw_meals:
            name = meal.get("name", "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())

            macros = self._macro_validator.validate_and_correct(
                {
                    "calories": meal.get("calories", 0),
                    "protein": meal.get("protein", 0),
                    "carbs": meal.get("carbs", 0),
                    "fat": meal.get("fat", 0),
                }
            )

            results.append(
                {
                    "id": f"disc_{uuid.uuid4().hex[:12]}",
                    "name": name,
                    "english_name": name,
                    "calories": macros["calories"],
                    "protein": macros["protein"],
                    "carbs": macros["carbs"],
                    "fat": macros["fat"],
                }
            )

            if len(results) >= count:
                break

        elapsed = time.time() - start
        logger.debug(
            f"[DISCOVERY-COMPLETE] session={session.id} | elapsed={elapsed:.2f}s | "
            f"returned={len(results)}"
        )

        if len(results) < count:
            results.extend(
                self._build_discovery_fallback(
                    session,
                    [*exclude_meal_names, *[r["name"] for r in results]],
                    count - len(results),
                    "partial or empty AI discovery response",
                )
            )

        return results

    def _build_discovery_fallback(
        self,
        session: SuggestionSession,
        exclude_meal_names: List[str],
        count: int,
        reason: str,
    ) -> List[dict]:
        meals = build_discovery_fallback_meals(
            session=session,
            exclude_meal_names=exclude_meal_names,
            count=count,
            macro_validator=self._macro_validator,
        )
        logger.info(
            "[DISCOVERY-FALLBACK-COMPLETE] session=%s | reason=%s | returned=%d",
            session.id,
            reason,
            len(meals),
        )
        return meals

    async def generate_selected_recipes(
        self,
        session: SuggestionSession,
        selected_meals: List[dict],
    ) -> List[MealSuggestion]:
        """Generate full recipes for discovery selections without scale rejection.

        2-pass retry: pass 1 generates all slots in parallel; pass 2 retries any
        None slots. Slots that still fail get a Discover-macro fallback recipe
        so the client can show ingredients and steps.
        """
        from src.domain.services.meal_suggestion.suggestion_prompt_builder import (
            build_recipe_details_prompt,
        )

        recipe_system = SystemPrompts.RECIPE_GENERATION

        async def generate_one(index: int, selected: dict) -> Optional[MealSuggestion]:
            target_calories = int(selected.get("calories") or session.target_calories)
            recipe_session = replace(
                session,
                target_calories=target_calories,
                protein_target=selected.get("protein") or session.protein_target,
                carbs_target=selected.get("carbs") or session.carbs_target,
                fat_target=selected.get("fat") or session.fat_target,
            )
            meal_name = selected.get("english_name") or selected.get("name")
            if not meal_name:
                raise ValueError("selected meal is missing english_name/name")
            prompt = build_recipe_details_prompt(meal_name, recipe_session)
            generated = await self._generate_with_retry(
                prompt,
                meal_name,
                index,
                recipe_system,
                recipe_session,
                reject_on_scale_out_of_range=False,
                fill_missing_steps=True,
                timeout=SELECTED_RECIPE_ATTEMPT_TIMEOUT,
            )
            localized_name = selected.get("name")
            if (
                generated is not None
                and localized_name
                and localized_name != meal_name
            ):
                generated = replace(generated, meal_name=localized_name)
            return generated

        results: List[Optional[MealSuggestion]] = [None] * len(selected_meals)

        # Pass 1: generate all slots in parallel
        pass1_tasks = [
            asyncio.create_task(generate_one(index, selected))
            for index, selected in enumerate(selected_meals)
        ]
        pass1_raw = await asyncio.gather(*pass1_tasks, return_exceptions=True)
        for i, result in enumerate(pass1_raw):
            if not isinstance(result, Exception) and result is not None:
                results[i] = result

        # Pass 2: retry any failed slots
        failed_indices = [i for i, r in enumerate(results) if r is None]
        if failed_indices:
            pass2_tasks = [
                asyncio.create_task(generate_one(i, selected_meals[i]))
                for i in failed_indices
            ]
            pass2_raw = await asyncio.gather(*pass2_tasks, return_exceptions=True)
            for i, result in zip(failed_indices, pass2_raw):
                if not isinstance(result, Exception) and result is not None:
                    results[i] = result

        for i, result in enumerate(results):
            if result is None:
                logger.warning(
                    "[PHASE-2-SELECTED-FALLBACK] index=%s | meal_id=%s",
                    i,
                    selected_meals[i].get("id"),
                )
                results[i] = fallback_selected_recipe(selected_meals[i], session)

        return [r for r in results if r is not None]

    async def _phase1_generate_names(
        self,
        session: SuggestionSession,
        exclude_meal_names: List[str],
        target_lang: str,
        suggestion_count: int = 3,
    ) -> List[str]:
        from src.domain.services.meal_suggestion.suggestion_prompt_builder import (
            build_meal_names_prompt,
        )

        meal_names_schema = self._meal_names_schema
        if not meal_names_schema:
            raise RuntimeError(
                "MealNamesResponse schema not provided to ParallelRecipeGenerator. "
                "Must be injected at initialization."
            )

        # Request extra names for headroom (failures, dedup); retry once on shortage
        names_to_generate = suggestion_count + 4
        logger.debug(
            f"[PHASE-1-START] session={session.id} | generating {names_to_generate} names in {target_lang} | "
            f"excluding {len(exclude_meal_names)} previous meals"
        )
        # Always generate names in English — Phase 3 translates to target language.
        # English names are preserved in MealSuggestion.english_name for image search.
        names_system = SystemPrompts.MEAL_NAMES_SYSTEM.format(count=names_to_generate)
        seen: set = set()
        meal_names: list = []
        max_attempts = 2
        try:
            for attempt in range(1, max_attempts + 1):
                names_raw = await asyncio.wait_for(
                    self._generation.generate_meal_plan_async(
                        build_meal_names_prompt(
                            session, exclude_meal_names, names_to_generate
                        ),
                        names_system,
                        "json",
                        1000,
                        meal_names_schema,
                        "meal_names",
                    ),
                    timeout=self.PHASE1_TIMEOUT,
                )
                for n in names_raw.get("meal_names", []):
                    if n.lower() not in seen:
                        seen.add(n.lower())
                        meal_names.append(n)
                if len(meal_names) >= suggestion_count:
                    break
                logger.warning(
                    f"[PHASE-1-RETRY] session={session.id} | attempt={attempt} | "
                    f"got {len(meal_names)} unique names, need {suggestion_count}"
                )
            if len(meal_names) < suggestion_count:
                raise RuntimeError(
                    f"Could not generate enough unique meal names "
                    f"(got {len(meal_names)}, need {suggestion_count})."
                )
            logger.debug(
                f"[PHASE-1-COMPLETE] session={session.id} | returned={len(meal_names)}"
            )
            return meal_names
        except Exception as e:
            logger.error(
                f"[PHASE-1-FAILED] session={session.id} | error_type={type(e).__name__}"
            )
            raise RuntimeError(f"Failed to generate meal names: {e}") from e

    async def _phase2_generate_recipes(
        self,
        session: SuggestionSession,
        meal_names: List[str],
        target_lang: str,
        suggestion_count: int = 3,
        min_acceptable_override: int = 0,
        preserve_order: bool = True,
    ) -> List[MealSuggestion]:
        from src.domain.services.meal_suggestion.suggestion_prompt_builder import (
            build_recipe_details_prompt,
        )

        total_attempts = len(meal_names)
        min_acceptable = min_acceptable_override or max(
            suggestion_count - 1, self.MIN_ACCEPTABLE_RESULTS
        )
        logger.debug(
            f"[PHASE-2-START] session={session.id} | recipes={len(meal_names)} | preserve_order={preserve_order}"
        )
        recipe_system = SystemPrompts.RECIPE_GENERATION
        prompts = [build_recipe_details_prompt(n, session) for n in meal_names]
        tasks = [
            asyncio.create_task(
                self._generate_with_retry(
                    prompts[i], meal_names[i], i, recipe_system, session
                )
            )
            for i in range(total_attempts)
        ]

        gen_start = time.time()
        successful: List[MealSuggestion] = []

        if preserve_order:
            # gather preserves submission order — required for /recipes endpoint where mobile
            # pairs results to selected meal names by index position.
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(
                        "[RECIPE-ERROR] error_type=%s", type(result).__name__
                    )
                elif result is not None:
                    successful.append(result)
        else:
            # as_completed yields in completion-time order, enabling early-stop once
            # suggestion_count successes arrive. Used by the discovery flow which spawns
            # total_attempts > suggestion_count tasks for headroom.
            for coro in asyncio.as_completed(tasks):
                try:
                    result = await coro
                    if result is not None:
                        successful.append(result)
                        if len(successful) >= suggestion_count:
                            cancelled = sum(
                                1 for t in tasks if not t.done() and t.cancel()
                            )
                            logger.debug(
                                f"[EARLY-STOP] Got {suggestion_count} meals, cancelled {cancelled} tasks"
                            )
                            break
                except Exception as e:
                    logger.warning("[RECIPE-ERROR] error_type=%s", type(e).__name__)

        logger.debug(
            f"[PHASE-2-COMPLETE] session={session.id} | "
            f"success={len(successful)}/{total_attempts} | elapsed={time.time()-gen_start:.2f}s"
        )
        if len(successful) < min_acceptable:
            if not successful:
                raise RuntimeError(
                    f"Failed to generate any recipes from {total_attempts} attempts"
                )
            raise RuntimeError(
                f"Insufficient recipes: {len(successful)}/{min_acceptable} minimum"
            )
        if len(successful) < suggestion_count:
            logger.warning(
                f"[PHASE-2-PARTIAL] session={session.id} | returning {len(successful)}/{suggestion_count} meals"
            )
        return successful

    async def _generate_with_retry(
        self,
        prompt: str,
        meal_name: str,
        index: int,
        recipe_system: str,
        session: SuggestionSession,
        reject_on_scale_out_of_range: bool = True,
        fill_missing_steps: bool = False,
        timeout: float | None = None,
    ) -> Optional[MealSuggestion]:
        """Try recipe model; retry on failure."""
        result = await attempt_recipe_generation(
            self._generation,
            self._macro_validator,
            self._nutrition_lookup,
            prompt,
            meal_name,
            index,
            "recipe",
            recipe_system,
            session,
            reject_on_scale_out_of_range=reject_on_scale_out_of_range,
            fill_missing_steps=fill_missing_steps,
            recipe_schema=self._recipe_details_schema,
            timeout=timeout,
        )
        if result is not None:
            return result
        logger.debug(f"[PHASE-2-RETRY] index={index}")
        return await attempt_recipe_generation(
            self._generation,
            self._macro_validator,
            self._nutrition_lookup,
            prompt,
            meal_name,
            index,
            "recipe",
            recipe_system,
            session,
            is_retry=True,
            reject_on_scale_out_of_range=reject_on_scale_out_of_range,
            fill_missing_steps=fill_missing_steps,
            recipe_schema=self._recipe_details_schema,
            timeout=timeout,
        )

    async def _translate_single(
        self, suggestion: MealSuggestion, language: str
    ) -> MealSuggestion:
        """Translate a single suggestion; return original on failure."""
        if self._translation_service is None:
            logger.debug("[TRANSLATE-SKIP] No translation service configured")
            return suggestion
        try:
            result = await self._translate_single_result(suggestion, language)
            return result.suggestion
        except Exception as exc:
            logger.warning(
                "[TRANSLATE-SINGLE-FAILED] suggestion_id=%s error_type=%s using_english=true",
                getattr(suggestion, "id", "unknown"),
                type(exc).__name__,
            )
            return suggestion

    async def _translate_single_result(
        self, suggestion: MealSuggestion, language: str
    ) -> SuggestionTranslationResult:
        if self._translation_service is None:
            return SuggestionTranslationResult(suggestion, TranslationOutcome.PASSTHROUGH)
        result_method = getattr(
            self._translation_service, "translate_meal_suggestion_result", None
        )
        if result_method is not None and inspect.iscoroutinefunction(result_method):
            return await result_method(suggestion, language)
        results = await self._translation_service.translate_meal_suggestions_batch(
            [suggestion], language
        )
        translated = results[0] if results else suggestion
        # Legacy list-returning doubles do not carry a provider outcome, so
        # render the result but never treat it as persistable localized data.
        return SuggestionTranslationResult(translated, TranslationOutcome.PARTIAL)

    async def _phase2_and_translate(
        self,
        session: SuggestionSession,
        meal_names: List[str],
        target_lang: str,
        suggestion_count: int,
    ) -> List[MealSuggestion]:
        """Pipeline Phase 2 + translation: translate each recipe as it completes."""
        from src.domain.services.meal_suggestion.suggestion_prompt_builder import (
            build_recipe_details_prompt,
        )

        recipe_system = SystemPrompts.RECIPE_GENERATION
        prompts = [build_recipe_details_prompt(n, session) for n in meal_names]
        gen_tasks = [
            asyncio.create_task(
                self._generate_with_retry(
                    prompts[i], meal_names[i], i, recipe_system, session
                )
            )
            for i in range(len(meal_names))
        ]

        translate_tasks: List[asyncio.Task] = []
        gen_successes = 0
        min_acceptable = max(suggestion_count - 1, self.MIN_ACCEPTABLE_RESULTS)

        logger.debug(
            f"[PHASE-2-PIPELINE-START] session={session.id} | recipes={len(meal_names)}"
        )
        gen_start = time.time()

        for coro in asyncio.as_completed(gen_tasks):
            try:
                result = await coro
                if result is not None:
                    gen_successes += 1
                    translate_tasks.append(
                        asyncio.create_task(
                            self._translate_single_result(result, session.language)
                        )
                    )
                    if gen_successes >= suggestion_count:
                        cancelled = sum(
                            1 for t in gen_tasks if not t.done() and t.cancel()
                        )
                        logger.debug(
                            f"[EARLY-STOP] Got {suggestion_count} recipes, "
                            f"cancelled {cancelled} gen tasks"
                        )
                        break
            except Exception as e:
                logger.warning("[RECIPE-ERROR] error_type=%s", type(e).__name__)

        logger.debug(
            f"[PHASE-2-PIPELINE-COMPLETE] session={session.id} | "
            f"success={gen_successes}/{len(meal_names)} | elapsed={time.time()-gen_start:.2f}s"
        )

        if gen_successes < min_acceptable:
            for t in translate_tasks:
                t.cancel()
            if not gen_successes:
                raise RuntimeError(
                    f"Failed to generate any recipes from {len(meal_names)} attempts"
                )
            raise RuntimeError(
                f"Insufficient recipes: {gen_successes}/{min_acceptable} minimum"
            )

        translated_results = await asyncio.gather(*translate_tasks)
        return [result.suggestion for result in translated_results]
