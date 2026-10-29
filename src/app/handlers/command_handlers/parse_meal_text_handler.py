"""
Handler for parsing natural language meal text into structured food items.
"""

import asyncio
import copy
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.events.base import EventHandler, handles
from src.app.handlers.command_handlers.meal_text_parsing_utils import (
    extract_json_from_response,
    parse_fatsecret_nutrition,
)
from src.app.schemas.meal_schemas import ParsedFoodItemDto, ParseMealTextResponseDto
from src.app.services.food_display_name import (
    apply_glossary_display_names,
    needs_display_localization,
)
from src.app.services.item_nutrition_cascade import (
    ParseTextRequestBudget as _ParseTextRequestBudget,
)
from src.app.services.item_nutrition_cascade import (
    canonicalize_reference_quantity as _canonicalize_reference_quantity,
)
from src.app.services.item_nutrition_cascade import (
    cascade_lookup as _shared_cascade_lookup,
)
from src.app.services.item_nutrition_cascade import (
    derive_calories_from_macros as _shared_derive_calories_from_macros,
)
from src.app.services.item_nutrition_cascade import (
    extract_english_name as _shared_extract_english_name,
)
from src.app.services.item_nutrition_cascade import (
    provider_call as _shared_provider_call,
)
from src.app.services.item_nutrition_cascade import (
    quantity_in_grams as _shared_quantity_in_grams,
)
from src.app.services.item_nutrition_cascade import (
    resolve_legacy_provider as _shared_resolve_legacy_provider,
)
from src.app.services.parse_text_custom_estimate import apply_custom_estimate
from src.app.services.parse_text_response_cache import (
    parse_text_cache_key,
    read_parse_text_cache,
    write_parse_text_cache,
)
from src.app.services.serving_label_localizer import localize_item_servings
from src.domain.exceptions.ai_exceptions import (
    AIOutputValidationError,
    AIUnavailableError,
)
from src.domain.model.ai.nutrition_contracts import (
    MealTextNutritionResponse,
)
from src.domain.model.nutrition.extra_nutrients import (
    extra_nutrients_to_micros,
    extras_from_portion_micros,
)
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.ai_output_validation_service import (
    build_parse_text_correction_prompt,
    validate_ai_output,
    validate_parse_text_nutrition,
)
from src.domain.services.emoji_validator import validate_emoji
from src.domain.services.nutrition_calculation_service import (
    fallback_custom_serving_options,
    scale_per_100g_nutrition,
)
from src.domain.services.nutrition_integrity_policy import (
    NUTRITION_INTEGRITY_POLICY_VERSION,
)
from src.domain.services.nutrition_resolver import normalize_food_lookup_name
from src.domain.services.prompts.input_sanitizer import (
    sanitize_user_description,
    validate_refinement_items,
)
from src.domain.services.prompts.system_prompts import SystemPrompts

logger = logging.getLogger(__name__)
PARSE_TEXT_VALIDATION_PURPOSE = "parse_text"
MAX_VALIDATION_ATTEMPTS = 2
PARSE_TEXT_RESPONSE_RESERVE_SECONDS = 0.3
CACHE_STAGE_TIMEOUT_SECONDS = 0.05


def _parse_text_fatsecret_timeout_seconds() -> float:
    try:
        return max(0.01, float(os.getenv("PARSE_TEXT_FATSECRET_TIMEOUT_SECONDS", "1")))
    except ValueError:
        return 1.0


@handles(ParseMealTextCommand)
class ParseMealTextHandler(
    EventHandler[ParseMealTextCommand, ParseMealTextResponseDto]
):
    """Handler for parsing meal text descriptions using AI."""

    def __init__(
        self,
        meal_generation_service: MealGenerationServicePort,
        fat_secret_service: Any | None = None,
        food_reference_batch_lookup: Any | None = None,
        structured_reference_enabled: bool = True,
        cache_service: Any | None = None,
        cache_ttl_seconds: int = 3600,
        uow_factory: Any | None = None,
        pure_ai_mode: bool | None = None,
        ai_timeout_seconds: float = 10.0,
        cache_namespace: str = "",
        reference_timeout_seconds: float | None = None,
    ):
        self._meal_generation_service = meal_generation_service
        self._fat_secret_service = fat_secret_service
        self._food_reference_batch_lookup = food_reference_batch_lookup
        self._structured_reference_enabled = structured_reference_enabled
        self._cache_service = cache_service
        self._cache_ttl_seconds = cache_ttl_seconds
        self._uow_factory = uow_factory
        self._ai_timeout_seconds = ai_timeout_seconds
        self._cache_namespace = cache_namespace
        self._reference_timeout_seconds = reference_timeout_seconds
        self._pure_ai_mode = (
            pure_ai_mode
            if pure_ai_mode is not None
            else (fat_secret_service is None and food_reference_batch_lookup is None)
        )

    async def handle(self, command: ParseMealTextCommand) -> ParseMealTextResponseDto:
        started = time.monotonic()
        budget = _ParseTextRequestBudget()
        status = "error"
        budget.request_deadline = started + self._ai_timeout_seconds
        try:
            response = await asyncio.wait_for(
                self._handle(command, budget), timeout=self._ai_timeout_seconds
            )
            status = "success"
            return response
        except TimeoutError as exc:
            raise AIUnavailableError(
                "Parse-text request deadline exceeded", last_error="timeout"
            ) from exc
        finally:
            logger.info(
                "[PARSE-TEXT-COMPLETE] status=%s mode=%s refinement=%s "
                "cache_hit=%s total_ms=%.1f ai_ms=%.1f reference_ms=%.1f "
                "ai_generations=%s provider_searches=%s provider_details=%s",
                status,
                "pure_ai" if self._pure_ai_mode else "reference",
                bool(command.current_items),
                budget.cache_hit,
                (time.monotonic() - started) * 1000,
                budget.ai_seconds * 1000,
                budget.reference_seconds * 1000,
                budget.ai_generations,
                budget.provider_searches,
                budget.provider_details,
            )

    async def _handle(
        self, command: ParseMealTextCommand, budget: _ParseTextRequestBudget
    ) -> ParseMealTextResponseDto:
        # Sanitize user input
        sanitized_text = sanitize_user_description(command.text)
        if not sanitized_text:
            raise ValueError("Invalid or empty meal description.")
        validated_current_items = validate_refinement_items(command.current_items)

        # Check utterance query cache for non-refinement queries
        system_prompt = SystemPrompts.get_meal_text_parsing_prompt(
            language=command.language
        )
        cache_context = (
            f"{self._cache_namespace}:{self._pure_ai_mode}:"
            f"{self._structured_reference_enabled}:{command.user_id is None}:"
            f"{NUTRITION_INTEGRITY_POLICY_VERSION}:{system_prompt}"
        )
        cache_key = parse_text_cache_key(
            sanitized_text, command.language, cache_context
        )
        if not validated_current_items:
            cache_time = min(
                CACHE_STAGE_TIMEOUT_SECONDS,
                max(
                    0.0,
                    (budget.request_deadline or time.monotonic())
                    - time.monotonic()
                    - PARSE_TEXT_RESPONSE_RESERVE_SECONDS,
                ),
            )
            cached_response = await read_parse_text_cache(
                self._cache_service, cache_key, timeout_seconds=cache_time
            )
            if cached_response is not None:
                budget.cache_hit = True
                return cached_response

        # Add refinement context if current_items provided
        if validated_current_items:
            context = json.dumps(validated_current_items, ensure_ascii=False)
            sanitized_text += (
                f"\n\nCurrent meal items (amounts and macros; prior micros are not "
                f"included):\n{context}\n\n"
                "Apply my request and return the complete updated list. Preserve "
                "each unchanged item's supplied name, amount, unit, and macros. "
                "Add or remove requested foods once. For an amount-only edit, "
                "scale that item's supplied macros by the amount ratio and keep "
                "other items unchanged. Estimate micros for each final food, "
                "state, and amount; prior micros are absent, so exact micro "
                "preservation is not possible."
            )

        user_envelope = f"language: {command.language or 'en'}\nmeal: {sanitized_text}"
        ai_started = time.monotonic()
        try:
            validated_payload, raw_payload = await asyncio.wait_for(
                self._generate_parse_text_payload(
                    prompt=user_envelope,
                    system_prompt=system_prompt,
                    budget=budget,
                ),
                timeout=max(
                    0.001,
                    (budget.request_deadline or time.monotonic())
                    - time.monotonic()
                    - PARSE_TEXT_RESPONSE_RESERVE_SECONDS,
                ),
            )
        except TimeoutError as exc:
            raise AIUnavailableError(
                "Parse-text AI deadline exceeded", last_error="timeout"
            ) from exc
        finally:
            budget.ai_seconds = time.monotonic() - ai_started
        emoji = validate_emoji(validated_payload.get("emoji"))
        parsed_items = self._to_flat_parse_text_items(validated_payload, raw_payload)
        enhanced_items = []
        if self._pure_ai_mode:
            for item in parsed_items:
                enhanced_items.append(self._require_custom_estimate(item))
        else:
            reference_started = time.monotonic()
            if budget.deadline is None:
                budget.deadline = min(
                    budget.request_deadline or time.monotonic(),
                    (budget.request_deadline or time.monotonic())
                    - PARSE_TEXT_RESPONSE_RESERVE_SECONDS,
                    time.monotonic()
                    + (
                        self._reference_timeout_seconds
                        if self._reference_timeout_seconds is not None
                        else _parse_text_fatsecret_timeout_seconds()
                    ),
                )
            local_references = await self._find_local_references(
                parsed_items, budget, command.language
            )

            async def _lookup_one(item: dict[str, Any]) -> dict[str, Any] | None:
                return await self._cascade_lookup(
                    copy.deepcopy(item),
                    budget=budget,
                    local_reference=local_references.get(
                        normalize_food_lookup_name(
                            item.get("lookup_name") or item.get("name", "")
                        )
                    ),
                )

            results = await asyncio.gather(
                *(_lookup_one(item) for item in parsed_items),
                return_exceptions=True,
            )
            for original, result in zip(parsed_items, results, strict=True):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    logger.debug(
                        "parse_text cascade lookup failed: %s",
                        type(result).__name__,
                    )
                    result = None
                if result is not None:
                    try:
                        self._validate_resolved_item(result)
                    except AIOutputValidationError:
                        result = None
                enhanced_items.append(result or self._require_custom_estimate(original))
            budget.reference_seconds = time.monotonic() - reference_started

        # Portions have already passed physical validation; keep them unchanged.
        for item in enhanced_items:
            self._retain_canonical_name(item)
            self._attach_per_100g_snapshot(item)

        # Calculate totals
        total_protein = sum(item.get("protein", 0) for item in enhanced_items)
        total_carbs = sum(item.get("carbs", 0) for item in enhanced_items)
        total_fat = sum(item.get("fat", 0) for item in enhanced_items)

        # Keep AI food identities when no deterministic display translation exists.
        if command.language and command.language != "en":
            for item in enhanced_items:
                item["name"] = self._extract_display_name(
                    item.get("name", "Unknown"), command.language
                )
            apply_glossary_display_names(enhanced_items, command.language)

        if not self._pure_ai_mode:
            finalization_started = time.monotonic()
            remaining = (budget.deadline or finalization_started) - finalization_started
            if remaining > 0:
                try:
                    async with asyncio.timeout(remaining):
                        await self._adopt_fatsecret_items(enhanced_items, command)
                        if command.language and command.language != "en":
                            await localize_item_servings(
                                enhanced_items,
                                language=command.language,
                                translation_service=None,
                                uow_factory=self._uow_factory,
                                persist=True,
                            )
                except TimeoutError:
                    logger.debug("parse_text optional finalization deadline reached")
            budget.reference_seconds += time.monotonic() - finalization_started

        # Serving localization changes display labels, not their gram weights.
        # Keep the full response cache snapshot in sync with those labels.
        for item in enhanced_items:
            snapshot = item.get("source_snapshot")
            if isinstance(snapshot, dict) and item.get("allowed_units") is not None:
                snapshot["allowed_units"] = copy.deepcopy(item["allowed_units"])

        # Build response items
        items = [
            ParsedFoodItemDto(
                name=item.get("name", "Unknown"),
                quantity=item.get("quantity", 1),
                unit=item.get("unit", "serving"),
                protein=item.get("protein", 0),
                carbs=item.get("carbs", 0),
                fat=item.get("fat", 0),
                fiber=item.get("fiber", 0),
                sugar=item.get("sugar", 0),
                data_source=item.get("data_source"),
                fdc_id=item.get("fdc_id"),
                allowed_units=item.get("allowed_units")
                or fallback_custom_serving_options(
                    item.get("unit") or "serving",
                    item.get("name") or "",
                ),
                food_id=item.get("food_id"),
                food_reference_id=item.get("food_reference_id"),
                origin=item.get("origin"),
                source_namespace=item.get("source_namespace"),
                source_food_id=item.get("source_food_id"),
                nutrition_basis=item.get("nutrition_basis"),
                nutrition_contract_version=item.get("nutrition_contract_version"),
                calories_per_100g=item.get("calories_per_100g"),
                protein_per_100g=item.get("protein_per_100g"),
                carbs_per_100g=item.get("carbs_per_100g"),
                fat_per_100g=item.get("fat_per_100g"),
                fiber_per_100g=item.get("fiber_per_100g"),
                sugar_per_100g=item.get("sugar_per_100g"),
                canonical_name=item.get("canonical_name"),
                source_snapshot=item.get("source_snapshot"),
            )
            for item in enhanced_items
        ]

        response_dto = ParseMealTextResponseDto(
            items=items,
            total_protein=total_protein,
            total_carbs=total_carbs,
            total_fat=total_fat,
            emoji=emoji,
            unmatched_terms=[],
        )
        if not validated_current_items:
            cache_time = min(
                CACHE_STAGE_TIMEOUT_SECONDS,
                max(
                    0.0,
                    (budget.request_deadline or time.monotonic())
                    - time.monotonic()
                    - PARSE_TEXT_RESPONSE_RESERVE_SECONDS,
                ),
            )
            await write_parse_text_cache(
                self._cache_service,
                cache_key,
                response_dto,
                self._cache_ttl_seconds,
                timeout_seconds=cache_time,
            )

        return response_dto

    @staticmethod
    def _require_custom_estimate(item: dict[str, Any]) -> dict[str, Any]:
        resolved = apply_custom_estimate(item)
        if resolved is None:
            raise AIOutputValidationError(
                "Parse-text item has no valid nutrition estimate",
                purpose="parse_text",
                attempt_count=1,
                validation_details=["items: invalid custom portion"],
            )
        return resolved

    def _validate_resolved_item(self, item: dict[str, Any]) -> None:
        grams = self._quantity_in_grams(
            item, str(item.get("lookup_name") or item["name"])
        )
        micros = item.get("portion_micros")
        if item.get("extra_nutrients"):
            effective_micros = extra_nutrients_to_micros(
                item["extra_nutrients"], factor=grams / 100
            )
            micros = effective_micros.to_dict() if effective_micros else None
        validate_parse_text_nutrition(
            {
                "items": [
                    {
                        **item,
                        "quantity_g": grams,
                        "macros": {
                            f"{key}_g": item.get(key, 0)
                            for key in ("protein", "carbs", "fat", "fiber", "sugar")
                        },
                        "micros": micros,
                    }
                ]
            }
        )

    def _attach_per_100g_snapshot(self, item: dict[str, Any]) -> None:
        """Expose the validated density used to derive the displayed portion."""
        allowed_units = item.get("allowed_units") or fallback_custom_serving_options(
            item.get("unit") or "serving", item.get("name") or ""
        )
        item["allowed_units"] = allowed_units
        quantity_g = item.get("quantity_g")
        if quantity_g is None:
            quantity_g = self._quantity_in_grams(
                item,
                str(item.get("lookup_name") or item.get("name") or ""),
            )
        quantity_g = float(quantity_g)
        if quantity_g <= 0:
            return
        factor = 100.0 / quantity_g
        for absolute, density in (
            ("protein", "protein_per_100g"),
            ("carbs", "carbs_per_100g"),
            ("fat", "fat_per_100g"),
            ("fiber", "fiber_per_100g"),
            ("sugar", "sugar_per_100g"),
        ):
            if item.get(density) is None:
                item[density] = round(float(item.get(absolute) or 0.0) * factor, 4)
        if item.get("calories_per_100g") is None:
            item["calories_per_100g"] = round(
                self._derive_calories_from_macros(
                    {
                        "protein_g": item["protein_per_100g"],
                        "carbs_g": item["carbs_per_100g"],
                        "fat_g": item["fat_per_100g"],
                        "fiber_g": item["fiber_per_100g"],
                    }
                ),
                4,
            )
        snapshot = {
            "basis": "100g",
            "protein_per_100g": item["protein_per_100g"],
            "carbs_per_100g": item["carbs_per_100g"],
            "fat_per_100g": item["fat_per_100g"],
            "fiber_per_100g": item["fiber_per_100g"],
            "sugar_per_100g": item["sugar_per_100g"],
            "calories_per_100g": item["calories_per_100g"],
            "allowed_units": copy.deepcopy(allowed_units),
            "origin": item.get("origin"),
            "source_namespace": item.get("source_namespace"),
            "source_food_id": item.get("source_food_id"),
        }
        extras = item.get("extra_nutrients")
        if not extras:
            extras = extras_from_portion_micros(item.get("portion_micros"), quantity_g)
            if extras:
                item["extra_nutrients"] = extras
        if extras:
            snapshot["extra_nutrients"] = extras
        canonical_name = item.get("canonical_name")
        if isinstance(canonical_name, str) and canonical_name.strip():
            snapshot["canonical_name"] = canonical_name.strip()
        item["source_snapshot"] = snapshot

    def _retain_canonical_name(self, item: dict[str, Any]) -> None:
        """Keep English catalog identity before display-name localization."""
        existing = str(item.get("canonical_name") or "").strip()
        if existing:
            item["canonical_name"] = existing
            return
        for key in ("food_name", "description"):
            candidate = item.get(key)
            if isinstance(candidate, str) and candidate.strip():
                item["canonical_name"] = candidate.strip()
                return
        lookup_name = str(item.get("lookup_name") or "").strip()
        if lookup_name:
            item["canonical_name"] = lookup_name
            return
        extracted = self._extract_english_name(str(item.get("name") or "")).strip()
        if extracted:
            item["canonical_name"] = extracted

    async def _generate_parse_text_payload(
        self,
        *,
        prompt: str,
        system_prompt: str,
        budget: _ParseTextRequestBudget | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        retry_system_prompt = system_prompt
        original_prompt = prompt
        previous_response = None
        for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
            if budget is not None:
                if budget.ai_generations >= 2:
                    raise AIOutputValidationError(
                        "AI generation budget exhausted",
                        purpose=PARSE_TEXT_VALIDATION_PURPOSE,
                        attempt_count=budget.ai_generations,
                        validation_details=["ai_generation_budget_exhausted"],
                    )
                budget.ai_generations += 1
            try:
                raw = await self._meal_generation_service.generate_meal_plan_async(
                    prompt=prompt,
                    system_message=retry_system_prompt,
                    response_type="json",
                    max_tokens=8192,
                    schema=MealTextNutritionResponse,
                    model_purpose="parse_text",
                    thinking_budget=0,
                )
                raw_payload = self._extract_parse_text_payload(raw)
                previous_response = raw_payload
                validated_payload = validate_ai_output(
                    raw_payload,
                    schema=MealTextNutritionResponse,
                    purpose=PARSE_TEXT_VALIDATION_PURPOSE,
                    attempt_count=attempt,
                )
                validate_parse_text_nutrition(validated_payload, attempt_count=attempt)
                if attempt > 1:
                    logger.info(
                        "[AI-OUTPUT-VALIDATION-RETRY-SUCCESS] purpose=%s attempt=%s",
                        PARSE_TEXT_VALIDATION_PURPOSE,
                        attempt,
                    )
                return validated_payload, raw_payload
            except (ConnectionError, TimeoutError) as exc:
                raise AIUnavailableError(
                    "Upstream connection failed during parse_text",
                    last_error=type(exc).__name__,
                ) from exc
            except AIOutputValidationError as exc:
                if previous_response is None and exc.candidate_payload:
                    try:
                        previous_response = self._extract_parse_text_payload(
                            json.loads(exc.candidate_payload)
                        )
                    except (ValueError, TypeError, AIOutputValidationError):
                        previous_response = None
                logger.warning(
                    "[AI-OUTPUT-VALIDATION-FAILED] purpose=%s attempt=%s details=%s",
                    PARSE_TEXT_VALIDATION_PURPOSE,
                    attempt,
                    exc.validation_details,
                )
                if attempt >= MAX_VALIDATION_ATTEMPTS:
                    raise AIOutputValidationError(
                        "Invalid AI output after validation retry",
                        purpose=PARSE_TEXT_VALIDATION_PURPOSE,
                        attempt_count=attempt,
                        validation_details=exc.validation_details,
                    ) from exc
                prompt = build_parse_text_correction_prompt(
                    original_prompt, previous_response, exc
                )
                retry_system_prompt = system_prompt

        raise RuntimeError("Failed to parse meal text after validation retry")

    def _extract_parse_text_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            payload = dict(raw)
        elif isinstance(raw, list):
            payload = {"items": raw}
        else:
            try:
                extracted = extract_json_from_response(str(raw))
            except ValueError as exc:
                raise AIOutputValidationError(
                    "Invalid AI structured output",
                    purpose=PARSE_TEXT_VALIDATION_PURPOSE,
                    attempt_count=1,
                    validation_details=["unparseable_ai_json"],
                ) from exc
            payload = {"items": extracted} if isinstance(extracted, list) else extracted

        if not isinstance(payload, dict):
            raise AIOutputValidationError(
                "Invalid AI structured output",
                purpose=PARSE_TEXT_VALIDATION_PURPOSE,
                attempt_count=1,
                validation_details=["response root must be an object or item list"],
            )
        return payload

    def _to_flat_parse_text_items(
        self, validated_payload: dict[str, Any], raw_payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        flat_items = []
        raw_items = raw_payload.get("items", [])
        for index, item in enumerate(validated_payload.get("items", [])):
            macros = item.get("macros", {})
            flat_item = {
                "name": item.get("name"),
                "model_display_name": item.get("name"),
                "lookup_name": item.get("lookup_name")
                or self._extract_english_name(item.get("name", "")),
                "preparation": item.get("preparation", "unknown"),
                "quantity": item.get("quantity"),
                "unit": item.get("unit"),
                "english_unit": item.get("english_unit"),
                "protein": macros.get("protein_g", 0.0),
                "carbs": macros.get("carbs_g", 0.0),
                "fat": macros.get("fat_g", 0.0),
                "fiber": macros.get("fiber_g", 0.0),
                "sugar": macros.get("sugar_g", 0.0),
                "calories": self._derive_calories_from_macros(macros),
            }
            raw_item = raw_items[index] if index < len(raw_items) else {}
            if isinstance(raw_item, dict):
                for key in ("english_name", "name_english", "canonical_name"):
                    english_name = raw_item.get(key)
                    if isinstance(english_name, str) and english_name.strip():
                        flat_item["english_name"] = english_name.strip()
                        break
            if item.get("quantity_g") is not None:
                flat_item["quantity_g"] = item["quantity_g"]
            micros = item.get("micros")
            if isinstance(micros, dict) and micros:
                flat_item["portion_micros"] = micros

            flat_items.append(flat_item)

        return flat_items

    @staticmethod
    def _derive_calories_from_macros(macros: dict[str, Any]) -> float:
        return _shared_derive_calories_from_macros(macros)

    async def _cascade_lookup(
        self,
        item: dict[str, Any],
        *,
        budget: _ParseTextRequestBudget,
        local_reference: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Resolve local catalog, then FatSecret; otherwise keep a custom g/kg estimate."""
        return await _shared_cascade_lookup(
            item,
            budget=budget,
            local_reference=local_reference,
            provider=self._fat_secret_service,
            structured_reference_enabled=self._structured_reference_enabled,
            parse_fatsecret_nutrition=parse_fatsecret_nutrition,
            scale_per_100g_nutrition=scale_per_100g_nutrition,
        )

    async def _resolve_legacy_provider(
        self,
        item: dict[str, Any],
        lookup_name: str,
        quantity: float,
        unit: str,
        budget: _ParseTextRequestBudget,
    ) -> dict[str, Any] | None:
        return await _shared_resolve_legacy_provider(
            item,
            lookup_name,
            quantity,
            unit,
            budget,
            self._fat_secret_service,
            parse_fatsecret_nutrition=parse_fatsecret_nutrition,
            scale_per_100g_nutrition=scale_per_100g_nutrition,
        )

    async def _provider_call(
        self,
        awaitable_factory: Callable[[], Awaitable[Any]],
        budget: _ParseTextRequestBudget,
    ) -> Any:
        return await _shared_provider_call(awaitable_factory, budget)

    def _quantity_in_grams(self, item: dict[str, Any], food_name: str) -> float:
        return _shared_quantity_in_grams(item, food_name)

    @staticmethod
    def _canonicalize_reference_quantity(
        item: dict[str, Any],
        *,
        quantity_g: float,
        allowed_units: list[dict[str, Any]],
    ) -> float:
        """Keep parsed portions saveable against the same source snapshot."""
        return _canonicalize_reference_quantity(
            item,
            quantity_g=quantity_g,
            allowed_units=allowed_units,
        )

    @staticmethod
    def _extract_english_name(name: str) -> str:
        """Extract English name for fatsecret lookup.

        AI may return either format:
        - 'English Name (Local Name)' → extract before parens
        - 'Local Name (English Name)' → extract inside parens
        Heuristic: if text inside parens is ASCII, it's English; otherwise
        the text before parens is English.
        """
        return _shared_extract_english_name(name)

    async def _find_local_references(
        self,
        items: list[dict[str, Any]],
        budget: _ParseTextRequestBudget,
        language: str,
    ) -> dict[str, dict[str, Any]]:
        key_by_display_name: dict[str, str] = {}
        keys: set[str] = set()
        for item in items:
            display_name = str(item.get("lookup_name") or item.get("name", "") or "")
            key = normalize_food_lookup_name(display_name)
            if not key:
                continue
            keys.add(key)
            for candidate in (display_name, str(item.get("name") or "")):
                candidate = candidate.strip()
                if candidate:
                    key_by_display_name.setdefault(candidate, key)
        if not keys:
            return {}
        deadline = budget.deadline
        if deadline is None or time.monotonic() >= deadline:
            return {}

        hits: dict[str, dict[str, Any]] = {}
        if self._food_reference_batch_lookup:
            try:
                remaining = max(0.01, deadline - time.monotonic())
                hits = await asyncio.wait_for(
                    self._food_reference_batch_lookup(sorted(keys)), timeout=remaining
                )
            except Exception as exc:
                logger.debug(
                    "parse_text local reference lookup failed: %s", type(exc).__name__
                )
                hits = {}

        missing_keys = keys - set(hits)
        if missing_keys and self._uow_factory is not None:
            missing_names = sorted(
                {
                    display_name
                    for display_name, key in key_by_display_name.items()
                    if key in missing_keys
                }
            )
            if missing_names and time.monotonic() < deadline:
                try:
                    remaining = max(0.01, deadline - time.monotonic())
                    locale_hits = await asyncio.wait_for(
                        self._locale_reference_lookup(language, missing_names),
                        timeout=remaining,
                    )
                except Exception as exc:
                    logger.debug(
                        "parse_text locale reference lookup failed: %s",
                        type(exc).__name__,
                    )
                    locale_hits = {}
                for display_name, row in (locale_hits or {}).items():
                    target_key = key_by_display_name.get(display_name)
                    if target_key and target_key not in hits:
                        hits[target_key] = row
        return hits

    async def _locale_reference_lookup(
        self, language: str, names: list[str]
    ) -> dict[str, dict[str, Any]]:
        if self._uow_factory is None:
            return {}
        async with self._uow_factory() as uow:
            return await uow.food_references.find_by_locale_names(language, names)

    async def _adopt_fatsecret_items(
        self,
        items: list[dict[str, Any]],
        command: ParseMealTextCommand,
    ) -> None:
        """Persist authenticated FatSecret hits so repeat parses hit the catalog.

        Guest parses (``user_id is None``) never write the catalog: unauthenticated
        adopt is a flood/poison vector against the shared food_reference table.
        """
        if command.user_id is None or self._uow_factory is None:
            return
        generic_sentinels = frozenset(
            {
                "nguyên liệu",
                "ingrediente",
                "ingrédient",
                "zutat",
                "食材",
                "ingredient",
                "unknown",
                "food",
                "thực phẩm",
                "món ăn",
            }
        )
        for item in items:
            if item.get("origin") != "provider":
                continue
            namespace = item.get("source_namespace")
            source_food_id = item.get("source_food_id")
            if not namespace or not source_food_id:
                continue
            english_name = str(
                item.get("canonical_name")
                or item.get("lookup_name")
                or item.get("name")
                or ""
            ).strip()
            if not english_name:
                continue

            raw_locale = str(
                item.get("model_display_name") or item.get("name") or ""
            ).strip()
            if not raw_locale or raw_locale.lower() in generic_sentinels:
                locale_name = ""
            elif (
                command.language
                and command.language != "en"
                and needs_display_localization(raw_locale, command.language)
            ):
                # Don't persist an English name as Vietnamese translation
                locale_name = ""
            else:
                locale_name = raw_locale

            per_100g = {
                "protein_100g": item.get("protein_per_100g") or 0.0,
                "carbs_100g": item.get("carbs_per_100g") or 0.0,
                "fat_100g": item.get("fat_per_100g") or 0.0,
                "fiber_100g": item.get("fiber_per_100g") or 0.0,
                "sugar_100g": item.get("sugar_per_100g") or 0.0,
            }
            extras = item.get("extra_nutrients")
            if isinstance(extras, dict) and extras:
                per_100g["extra_nutrients"] = extras
            servings = item.get("allowed_units") or None
            try:
                async with self._uow_factory() as uow:
                    adopted = await uow.food_references.adopt_provider_food(
                        str(namespace),
                        str(source_food_id),
                        english_name,
                        per_100g,
                        servings,
                        command.language,
                        locale_name,
                    )
            except Exception:
                logger.warning(
                    "parse_text adopt_provider_food failed namespace=%s food_id=%s",
                    namespace,
                    source_food_id,
                    exc_info=True,
                )
                continue
            reference_id = adopted.get("id") if isinstance(adopted, dict) else None
            if reference_id is None:
                continue
            item["food_reference_id"] = reference_id
            item["origin"] = "local"
            snapshot = item.get("source_snapshot")
            if isinstance(snapshot, dict):
                snapshot["origin"] = "local"

    @staticmethod
    def _extract_display_name(name: str, language: str) -> str:
        """Strip parenthesized portion, keep only the user's language.

        'Sliced Beef (Thịt bò)' + vi → 'Thịt bò'
        'Thịt bò (Sliced Beef)' + vi → 'Thịt bò'
        'Eggs' (no parens) → 'Eggs'
        """
        match = re.search(r"^(.+?)\s*\(([^)]+)\)$", name.strip())
        if not match:
            return name
        before, inside = match.group(1).strip(), match.group(2).strip()
        before_english = needs_display_localization(before, language)
        inside_english = needs_display_localization(inside, language)
        if before_english and not inside_english:
            return inside
        if inside_english and not before_english:
            return before
        return before
