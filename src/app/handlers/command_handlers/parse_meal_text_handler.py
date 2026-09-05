"""
Handler for parsing natural language meal text into structured food items.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.events.base import EventHandler, handles
from src.app.handlers.command_handlers.meal_text_parsing_utils import (
    extract_json_from_response,
    parse_fatsecret_nutrition,
)
from src.app.schemas.meal_schemas import ParsedFoodItemDto, ParseMealTextResponseDto
from src.app.services.food_display_name import (
    apply_fail_closed_display_names,
    apply_glossary_display_names,
    needs_display_localization,
)
from src.app.services.parse_text_custom_estimate import apply_custom_estimate
from src.domain.model.nutrition.extra_nutrients import extras_from_portion_micros
from src.app.services.serving_label_localizer import localize_item_servings
from src.domain.exceptions.ai_exceptions import AIOutputValidationError
from src.domain.model.ai.nutrition_contracts import (
    MealTextNutritionResponse,
)
from src.domain.model.nutrition.macros import Macros
from src.domain.ports.meal_generation_service_port import MealGenerationServicePort
from src.domain.services.ai_output_validation_service import (
    build_validation_retry_prompt,
    validate_ai_output,
)
from src.domain.services.emoji_validator import validate_emoji
from src.domain.services.nutrition_calculation_service import (
    MASS_VOLUME_CANONICAL_UNITS,
    UNIT_TO_GRAMS,
    _convert_with_allowed_units,
    _normalize_unit,
    canonicalize_mass_volume_unit,
    clamp_nutrition_values,
    convert_quantity_to_grams,
    fallback_custom_serving_options,
    normalize_unit_for_manual_save,
    scale_per_100g_nutrition,
)
from src.domain.services.nutrition_integrity_policy import (
    NUTRITION_INTEGRITY_POLICY_VERSION,
    normalize_serving_options,
)
from src.domain.services.nutrition_resolver import (
    normalize_food_lookup_name,
    preparation_matches,
    select_nutrition_candidate,
    validate_ai_fallback,
    validate_reference_candidate,
)
from src.domain.services.prompts.input_sanitizer import (
    sanitize_user_description,
    validate_refinement_items,
)
from src.domain.services.prompts.system_prompts import SystemPrompts

logger = logging.getLogger(__name__)
PARSE_TEXT_VALIDATION_PURPOSE = "parse_text"
MAX_VALIDATION_ATTEMPTS = 2
MAX_PROVIDER_SEARCHES = 5
MAX_PROVIDER_DETAILS = 5
MAX_PROVIDER_CONCURRENCY = 3
TRUSTED_QUANTITY_UNITS = {
    "g",
    "gram",
    "grams",
    "kg",
    "kilogram",
    "kilograms",
    "ml",
    "l",
    "liter",
    "litre",
    "oz",
    "ounce",
    "ounces",
    "lb",
    "pound",
    "pounds",
}
COUNTABLE_GRAM_UNITS = set(UNIT_TO_GRAMS) - {"kg", "lb", "oz"}


def _preferred_parse_unit(item: dict[str, Any]) -> str:
    """Prefer the local unit when it is a real mass, volume, or countable unit."""
    local = str(item.get("unit") or "").strip()
    english = str(item.get("english_unit") or "").strip()
    if local:
        canonical = canonicalize_mass_volume_unit(local)
        if canonical in MASS_VOLUME_CANONICAL_UNITS:
            return canonical
        local_norm = _normalize_unit(local)
        if local_norm in TRUSTED_QUANTITY_UNITS or local_norm in COUNTABLE_GRAM_UNITS:
            return local
    return english or local or "serving"


@dataclass
class _ParseTextRequestBudget:
    """Request-wide limits shared by AI and staged reference resolution."""

    deadline: float | None = None
    ai_generations: int = 0
    provider_searches: int = 0
    provider_details: int = 0
    semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_PROVIDER_CONCURRENCY)
    )


def _parse_text_fatsecret_timeout_seconds() -> float:
    try:
        return max(0.01, float(os.getenv("PARSE_TEXT_FATSECRET_TIMEOUT_SECONDS", "3")))
    except ValueError:
        return 3.0


def _parse_text_cache_key(text: str, language: str | None) -> str:
    norm_text = " ".join(text.strip().lower().split())
    norm_lang = (language or "en").strip().lower()
    digest = hashlib.sha256(norm_text.encode("utf-8")).hexdigest()[:24]
    return f"parse_text:{norm_lang}:{digest}"


@handles(ParseMealTextCommand)
class ParseMealTextHandler(
    EventHandler[ParseMealTextCommand, ParseMealTextResponseDto]
):
    """Handler for parsing meal text descriptions using AI."""

    def __init__(
        self,
        meal_generation_service: MealGenerationServicePort,
        fat_secret_service: Any | None = None,
        translation_service: Any | None = None,
        food_reference_batch_lookup: Any | None = None,
        structured_reference_enabled: bool = True,
        cache_service: Any | None = None,
        cache_ttl_seconds: int = 604800,
        uow_factory: Any | None = None,
        pure_ai_mode: bool | None = None,
    ):
        self._meal_generation_service = meal_generation_service
        self._fat_secret_service = fat_secret_service
        self._translation_service = translation_service
        self._food_reference_batch_lookup = food_reference_batch_lookup
        self._structured_reference_enabled = structured_reference_enabled
        self._cache_service = cache_service
        self._cache_ttl_seconds = cache_ttl_seconds
        self._uow_factory = uow_factory
        self._pure_ai_mode = (
            pure_ai_mode
            if pure_ai_mode is not None
            else (fat_secret_service is None and food_reference_batch_lookup is None)
        )

    async def handle(self, command: ParseMealTextCommand) -> ParseMealTextResponseDto:
        # Sanitize user input
        sanitized_text = sanitize_user_description(command.text)
        if not sanitized_text:
            raise ValueError("Invalid or empty meal description.")
        validated_current_items = validate_refinement_items(command.current_items)

        # Check utterance query cache for non-refinement queries
        cache_key = _parse_text_cache_key(sanitized_text, command.language)
        if not validated_current_items and self._cache_service:
            try:
                cached_data = await self._cache_service.get(cache_key)
                if isinstance(cached_data, dict) and "items" in cached_data:
                    cached_items = [
                        ParsedFoodItemDto(
                            name=item.get("name", "Unknown"),
                            quantity=float(item.get("quantity") or 1.0),
                            unit=item.get("unit", "serving"),
                            protein=float(item.get("protein") or 0.0),
                            carbs=float(item.get("carbs") or 0.0),
                            fat=float(item.get("fat") or 0.0),
                            fiber=float(item.get("fiber") or 0.0),
                            sugar=float(item.get("sugar") or 0.0),
                            data_source=item.get("data_source"),
                            fdc_id=item.get("fdc_id"),
                            allowed_units=item.get("allowed_units"),
                            food_id=item.get("food_id"),
                            food_reference_id=item.get("food_reference_id"),
                            origin=item.get("origin"),
                            source_namespace=item.get("source_namespace"),
                            source_food_id=item.get("source_food_id"),
                            nutrition_basis=item.get("nutrition_basis"),
                            nutrition_contract_version=item.get(
                                "nutrition_contract_version"
                            ),
                            calories_per_100g=item.get("calories_per_100g"),
                            protein_per_100g=item.get("protein_per_100g"),
                            carbs_per_100g=item.get("carbs_per_100g"),
                            fat_per_100g=item.get("fat_per_100g"),
                            fiber_per_100g=item.get("fiber_per_100g"),
                            sugar_per_100g=item.get("sugar_per_100g"),
                            canonical_name=item.get("canonical_name"),
                            source_snapshot=item.get("source_snapshot"),
                        )
                        for item in cached_data.get("items", [])
                    ]
                    return ParseMealTextResponseDto(
                        items=cached_items,
                        total_protein=float(cached_data.get("total_protein") or 0.0),
                        total_carbs=float(cached_data.get("total_carbs") or 0.0),
                        total_fat=float(cached_data.get("total_fat") or 0.0),
                        emoji=cached_data.get("emoji"),
                        unmatched_terms=cached_data.get("unmatched_terms") or [],
                    )
            except Exception as exc:
                logger.debug("parse_text cache read failed: %s", exc)

        # Add refinement context if current_items provided
        if validated_current_items:
            context = json.dumps(validated_current_items, ensure_ascii=False)
            sanitized_text += (
                f"\n\nCurrent meal items:\n{context}\n\n"
                "Update the meal based on my request above. Return the COMPLETE updated list."
            )

        # Build messages with locale-aware food names
        system_prompt = SystemPrompts.get_meal_text_parsing_prompt(
            language=command.language
        )

        user_envelope = f"language: {command.language or 'en'}\nmeal: {sanitized_text}"

        budget = _ParseTextRequestBudget()
        validated_payload, raw_payload = await self._generate_parse_text_payload(
            prompt=user_envelope,
            system_prompt=system_prompt,
            budget=budget,
        )
        emoji = validate_emoji(validated_payload.get("emoji"))
        parsed_items = self._to_flat_parse_text_items(validated_payload, raw_payload)
        enhanced_items = []
        if self._pure_ai_mode:
            for item in parsed_items:
                resolved = apply_custom_estimate(item) or item
                enhanced_items.append(resolved)
        else:
            if budget.deadline is None:
                budget.deadline = (
                    time.monotonic() + _parse_text_fatsecret_timeout_seconds()
                )
            local_references = await self._find_local_references(
                parsed_items, budget, command.language
            )
            for item in parsed_items:
                cascaded = await self._cascade_lookup(
                    item,
                    budget=budget,
                    local_reference=local_references.get(
                        normalize_food_lookup_name(
                            item.get("lookup_name") or item.get("name", "")
                        )
                    ),
                )
                if cascaded is not None:
                    enhanced_items.append(cascaded)

        # Clamp nutrition to physically plausible ranges
        for item in enhanced_items:
            clamped = clamp_nutrition_values(item)
            item.update(clamped)
            self._retain_canonical_name(item)
            self._attach_per_100g_snapshot(item)

        # Calculate totals
        total_protein = sum(item.get("protein", 0) for item in enhanced_items)
        total_carbs = sum(item.get("carbs", 0) for item in enhanced_items)
        total_fat = sum(item.get("fat", 0) for item in enhanced_items)

        # Localize names for non-English users (1-turn deterministic only)
        if command.language and command.language != "en":
            for item in enhanced_items:
                item["name"] = self._extract_display_name(
                    item.get("name", "Unknown"), command.language
                )
            apply_glossary_display_names(enhanced_items, command.language)
            apply_fail_closed_display_names(enhanced_items, command.language)

        if not self._pure_ai_mode:
            await self._adopt_fatsecret_items(enhanced_items, command)
            if command.language and command.language != "en":
                await localize_item_servings(
                    enhanced_items,
                    language=command.language,
                    translation_service=None,
                    uow_factory=self._uow_factory,
                    persist=True,
                )

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
        if not validated_current_items and self._cache_service:
            try:
                await self._cache_service.set(
                    cache_key,
                    asdict(response_dto),
                    ttl_seconds=self._cache_ttl_seconds,
                )
            except Exception as exc:
                logger.debug("parse_text cache write failed: %s", exc)

        return response_dto

    def _attach_per_100g_snapshot(self, item: dict[str, Any]) -> None:
        """Expose the validated density used to derive the displayed portion."""
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
            "allowed_units": item.get("allowed_units") or [],
            "origin": item.get("origin"),
            "source_namespace": item.get("source_namespace"),
            "source_food_id": item.get("source_food_id"),
        }
        extras = item.get("extra_nutrients")
        if not extras:
            extras = extras_from_portion_micros(
                item.get("portion_micros"), quantity_g
            )
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
                    max_tokens=2048,
                    schema=MealTextNutritionResponse,
                    model_purpose="parse_text",
                    thinking_budget=0,
                )
                raw_payload = self._extract_parse_text_payload(raw)
                validated_payload = validate_ai_output(
                    raw_payload,
                    schema=MealTextNutritionResponse,
                    purpose=PARSE_TEXT_VALIDATION_PURPOSE,
                    attempt_count=attempt,
                )
                if attempt > 1:
                    logger.info(
                        "[AI-OUTPUT-VALIDATION-RETRY-SUCCESS] purpose=%s attempt=%s",
                        PARSE_TEXT_VALIDATION_PURPOSE,
                        attempt,
                    )
                return validated_payload, raw_payload
            except AIOutputValidationError as exc:
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
                retry_system_prompt = build_validation_retry_prompt(system_prompt, exc)

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
        protein = float(macros.get("protein_g", 0.0) or 0.0)
        carbs = float(macros.get("carbs_g", 0.0) or 0.0)
        fiber = float(macros.get("fiber_g", 0.0) or 0.0)
        fat = float(macros.get("fat_g", 0.0) or 0.0)
        return round(Macros.raw_total_calories(protein, carbs, fat, fiber), 2)

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

    async def _cascade_lookup(
        self,
        item: dict[str, Any],
        *,
        budget: _ParseTextRequestBudget,
        local_reference: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Resolve local catalog, then FatSecret; otherwise keep a custom g/kg estimate."""
        name = str(item.get("name") or "")
        lookup_name = str(item.get("lookup_name") or self._extract_english_name(name))
        preparation = str(item.get("preparation") or "unknown")
        quantity = float(item.get("quantity") or 1.0)
        unit = item.get("english_unit") or item.get("unit", "serving")
        quantity_g = self._quantity_in_grams(item, lookup_name)

        if local_reference and self._local_reference_is_usable(
            local_reference, lookup_name, preparation
        ):
            candidate = validate_reference_candidate(
                local_reference,
                require_energy=False,
                require_metric_basis=False,
            )
            if candidate:
                return self._apply_reference(
                    item, candidate, quantity_g, "local_reference", local_reference
                )

        if self._fat_secret_service and self._structured_reference_enabled:
            structured = await self._resolve_staged_provider(
                item, lookup_name, preparation, quantity_g, budget
            )
            if structured is not None:
                return structured
        elif self._fat_secret_service:
            legacy = await self._resolve_legacy_provider(
                item, lookup_name, quantity, unit, budget
            )
            if legacy is not None:
                return legacy

        return apply_custom_estimate(item)

    async def _resolve_staged_provider(
        self,
        item: dict[str, Any],
        lookup_name: str,
        preparation: str,
        quantity_g: float,
        budget: _ParseTextRequestBudget,
    ) -> dict[str, Any] | None:
        provider = self._fat_secret_service
        if provider is None:
            return None
        if not hasattr(provider, "search_food_candidates"):
            return await self._resolve_legacy_provider(
                item,
                lookup_name,
                float(item.get("quantity") or 1),
                item.get("english_unit") or item.get("unit", "serving"),
                budget,
            )
        if budget.provider_searches >= MAX_PROVIDER_SEARCHES:
            return None
        budget.provider_searches += 1
        candidates = await self._provider_call(
            lambda: provider.search_food_candidates(lookup_name, max_results=5),
            budget,
        )
        if candidates is None:
            return None
        selected = select_nutrition_candidate(candidates, lookup_name, preparation)
        if not selected or not selected.get("food_id"):
            return None
        if budget.provider_details >= MAX_PROVIDER_DETAILS:
            return None
        budget.provider_details += 1
        details = await self._provider_call(
            lambda: provider.get_food_details(str(selected["food_id"])),
            budget,
        )
        if not isinstance(details, dict):
            return None
        merged = {**selected, **details}
        candidate = validate_reference_candidate(merged)
        if candidate is None:
            return None
        return self._apply_reference(item, candidate, quantity_g, "fatsecret", merged)

    async def _resolve_legacy_provider(
        self,
        item: dict[str, Any],
        lookup_name: str,
        quantity: float,
        unit: str,
        budget: _ParseTextRequestBudget,
    ) -> dict[str, Any] | None:
        provider = self._fat_secret_service
        if provider is None:
            return None
        if budget.provider_searches >= MAX_PROVIDER_SEARCHES:
            return None
        budget.provider_searches += 1
        try:
            results = await self._provider_call(
                lambda: provider.search_foods(lookup_name, max_results=5),
                budget,
            )
        except Exception as exc:
            logger.debug("parse_text legacy provider failed: %s", type(exc).__name__)
            return None
        if not results:
            return None
        selected = select_nutrition_candidate(results, lookup_name)
        if selected is None:
            return None
        if not self._reference_identity(selected, "fatsecret")["source_food_id"]:
            return None
        structured = validate_reference_candidate(selected, require_energy=False)
        per_100g = (
            {
                "calories_100g": selected.get("calories_100g"),
                "protein_100g": selected.get("protein_100g"),
                "carbs_100g": selected.get("carbs_100g"),
                "fat_100g": selected.get("fat_100g"),
                "fiber_100g": selected.get("fiber_100g", 0),
                "sugar_100g": selected.get("sugar_100g", 0),
            }
            if structured
            else parse_fatsecret_nutrition(selected)
        )
        if not per_100g:
            return None
        allowed_units = self._safe_allowed_units(selected.get("allowed_units"))
        quantity_g = self._canonicalize_reference_quantity(
            item,
            quantity_g=self._quantity_in_grams(item, lookup_name),
            allowed_units=allowed_units,
        )
        scaled = scale_per_100g_nutrition(
            {
                "calories": per_100g.get("calories_100g", per_100g.get("calories", 0)),
                "protein": per_100g.get("protein_100g", per_100g.get("protein", 0)),
                "carbs": per_100g.get("carbs_100g", per_100g.get("carbs", 0)),
                "fat": per_100g.get("fat_100g", per_100g.get("fat", 0)),
                "fiber": per_100g.get("fiber_100g", per_100g.get("fiber", 0)),
                "sugar": per_100g.get("sugar_100g", per_100g.get("sugar", 0)),
            },
            item["quantity"],
            item["unit"],
            allowed_units=allowed_units,
            food_name=lookup_name,
            strict_allowed_units=True,
        )
        if not validate_ai_fallback(
            name=lookup_name,
            protein=scaled.get("protein"),
            carbs=scaled.get("carbs"),
            fat=scaled.get("fat"),
            fiber=scaled.get("fiber"),
            quantity_g=quantity_g,
        ):
            return None
        item.update(scaled)
        item["calories"] = self._derive_calories_from_macros(item)
        item["allowed_units"] = allowed_units
        item["data_source"] = "fatsecret"
        extras = selected.get("extra_nutrients")
        if extras:
            item["extra_nutrients"] = extras
        item.update(self._reference_identity(selected, "fatsecret"))
        catalog_name = str(
            selected.get("food_name")
            or selected.get("description")
            or selected.get("name")
            or ""
        ).strip()
        if catalog_name:
            item["canonical_name"] = catalog_name
        item["nutrition_basis"] = "100g"
        item["nutrition_contract_version"] = NUTRITION_INTEGRITY_POLICY_VERSION
        item["calories_per_100g"] = self._derive_calories_from_macros(
            {
                "protein_g": per_100g.get("protein_100g", per_100g.get("protein", 0)),
                "carbs_g": per_100g.get("carbs_100g", per_100g.get("carbs", 0)),
                "fat_g": per_100g.get("fat_100g", per_100g.get("fat", 0)),
                "fiber_g": per_100g.get("fiber_100g", per_100g.get("fiber", 0)),
            }
        )
        item["protein_per_100g"] = per_100g.get(
            "protein_100g", per_100g.get("protein", 0)
        )
        item["carbs_per_100g"] = per_100g.get("carbs_100g", per_100g.get("carbs", 0))
        item["fat_per_100g"] = per_100g.get("fat_100g", per_100g.get("fat", 0))
        item["fiber_per_100g"] = per_100g.get("fiber_100g", per_100g.get("fiber", 0))
        item["sugar_per_100g"] = per_100g.get("sugar_100g", per_100g.get("sugar", 0))
        return item

    async def _provider_call(
        self,
        awaitable_factory: Callable[[], Awaitable[Any]],
        budget: _ParseTextRequestBudget,
    ) -> Any:
        deadline = budget.deadline
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        async with budget.semaphore:
            deadline = budget.deadline
            if deadline is None:
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                return await asyncio.wait_for(awaitable_factory(), timeout=remaining)
            except Exception as exc:
                logger.debug("parse_text provider call failed: %s", type(exc).__name__)
                return None

    def _quantity_in_grams(self, item: dict[str, Any], food_name: str) -> float:
        unit = _preferred_parse_unit(item)
        normalized_unit = normalize_unit_for_manual_save(unit)
        # Volume units must convert with food density, never assumed 1g = 1ml
        if normalized_unit in {"ml", "l", "cup", "tbsp", "tsp", "floz"}:
            quantity = float(item.get("quantity") or 1)
            return convert_quantity_to_grams(quantity, normalized_unit, food_name)
        if item.get("quantity_g") is not None:
            return float(item["quantity_g"])
        quantity = float(item.get("quantity") or 1)
        return convert_quantity_to_grams(quantity, normalized_unit, food_name)

    def _local_reference_is_usable(
        self, reference: dict[str, Any], lookup_name: str, preparation: str
    ) -> bool:
        if not reference.get("is_verified"):
            return False
        source = str(reference.get("source") or "").lower()
        if source not in {
            "usda",
            "usda_fdc",
            "fooddata_central",
            "fatsecret",
            "food_reference",
            "seed",
            "catalog_seed",
            "nin",
        }:
            return False
        return preparation_matches(
            str(reference.get("name") or lookup_name), preparation
        )

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

    def _apply_reference(
        self,
        item: dict[str, Any],
        candidate: Any,
        quantity_g: float,
        source: str,
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        allowed_units = self._safe_allowed_units(
            raw.get("allowed_units") or raw.get("serving_sizes")
        )
        if not allowed_units:
            allowed_units = list(candidate.allowed_units or [])
        quantity_g = self._canonicalize_reference_quantity(
            item,
            quantity_g=quantity_g,
            allowed_units=allowed_units,
        )
        factor = quantity_g / 100.0
        item.update(
            {
                "protein": round(candidate.protein_per_100g * factor, 2),
                "carbs": round(candidate.carbs_per_100g * factor, 2),
                "fat": round(candidate.fat_per_100g * factor, 2),
                "fiber": round(candidate.fiber_per_100g * factor, 2),
                "sugar": round(candidate.sugar_per_100g * factor, 2),
            }
        )
        item["calories"] = self._derive_calories_from_macros(item)
        item["data_source"] = (
            "fatsecret"
            if source == "fatsecret"
            or str(raw.get("source", "")).lower() == "fatsecret"
            else "usda"
        )
        item["fdc_id"] = raw.get("fdc_id")
        item.update(self._reference_identity(raw, source))
        catalog_name = str(
            raw.get("food_name")
            or raw.get("description")
            or raw.get("name")
            or item.get("lookup_name")
            or ""
        ).strip()
        if catalog_name:
            item["canonical_name"] = catalog_name
        item["nutrition_basis"] = "100g"
        item["nutrition_contract_version"] = NUTRITION_INTEGRITY_POLICY_VERSION
        item["calories_per_100g"] = self._derive_calories_from_macros(
            {
                "protein_g": candidate.protein_per_100g,
                "carbs_g": candidate.carbs_per_100g,
                "fat_g": candidate.fat_per_100g,
                "fiber_g": candidate.fiber_per_100g,
            }
        )
        item["protein_per_100g"] = candidate.protein_per_100g
        item["carbs_per_100g"] = candidate.carbs_per_100g
        item["fat_per_100g"] = candidate.fat_per_100g
        item["fiber_per_100g"] = candidate.fiber_per_100g
        item["sugar_per_100g"] = candidate.sugar_per_100g
        item["allowed_units"] = allowed_units
        extras = raw.get("extra_nutrients")
        if extras:
            item["extra_nutrients"] = extras
        return item

    @staticmethod
    def _canonicalize_reference_quantity(
        item: dict[str, Any],
        *,
        quantity_g: float,
        allowed_units: list[dict[str, Any]],
    ) -> float:
        """Keep parsed portions saveable against the same source snapshot."""
        quantity = float(item.get("quantity") or 1.0)
        source_unit = canonicalize_mass_volume_unit(
            str(item.get("english_unit") or item.get("unit") or "g")
        )
        try:
            authoritative_grams = _convert_with_allowed_units(
                quantity,
                source_unit,
                allowed_units,
                str(item.get("lookup_name") or item.get("name") or ""),
                strict=True,
            )
        except ValueError:
            item["quantity"] = quantity_g
            item["unit"] = "g"
            item["english_unit"] = "g"
            item["quantity_g"] = quantity_g
            return quantity_g

        item["unit"] = source_unit
        item["english_unit"] = source_unit
        item["quantity_g"] = authoritative_grams
        return authoritative_grams

    @staticmethod
    def _reference_identity(raw: dict[str, Any], source: str) -> dict[str, Any]:
        raw_source = str(raw.get("source") or source or "").lower()
        if (
            source == "local_reference"
            or raw_source == "food_reference"
            or raw.get("food_reference_id") is not None
        ):
            reference_id = raw.get("food_reference_id") or raw.get("id")
            return {
                "origin": "local",
                "food_reference_id": reference_id,
                "food_id": f"food_reference:{reference_id}",
                "source_namespace": "food_reference",
                "source_food_id": str(reference_id),
            }
        if raw.get("fdc_id") is not None or raw.get("fdcId") is not None:
            fdc_id = raw.get("fdc_id") or raw.get("fdcId")
            return {
                "origin": "usda",
                "food_id": f"usda_fdc:{fdc_id}",
                "source_namespace": "usda_fdc",
                "source_food_id": str(fdc_id),
            }
        namespace = str(raw.get("source_namespace") or raw_source or "fatsecret")
        source_id = str(raw.get("source_food_id") or raw.get("food_id") or "")
        if source_id.startswith(f"{namespace}:"):
            source_id = source_id[len(namespace) + 1 :]
        if not source_id:
            return {
                "origin": "provider",
                "food_id": None,
                "source_namespace": namespace,
                "source_food_id": None,
            }
        return {
            "origin": "provider",
            "food_id": f"{namespace}:{source_id}",
            "source_namespace": namespace,
            "source_food_id": source_id,
        }

    @staticmethod
    def _safe_allowed_units(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        units: list[dict[str, Any]] = []
        for entry in raw[:12]:
            if not isinstance(entry, dict):
                continue
            unit = str(entry.get("unit") or entry.get("name") or "").strip()
            description = str(entry.get("description") or "").strip()
            raw_gram_weight = entry.get("gram_weight") or entry.get("grams")
            if raw_gram_weight is None:
                continue
            try:
                gram_weight = float(raw_gram_weight)
            except (TypeError, ValueError):
                continue
            if (
                not unit
                or len(unit) > 100
                or len(description) > 100
                or not gram_weight > 0
                or not all(ord(char) >= 32 for char in unit + description)
            ):
                continue
            units.append(
                {
                    "unit": unit,
                    "gram_weight": gram_weight,
                    "description": description,
                }
            )
        return normalize_serving_options(units, provider_100g_label=True) or []

    @staticmethod
    def _extract_english_name(name: str) -> str:
        """Extract English name for fatsecret lookup.

        AI may return either format:
        - 'English Name (Local Name)' → extract before parens
        - 'Local Name (English Name)' → extract inside parens
        Heuristic: if text inside parens is ASCII, it's English; otherwise
        the text before parens is English.
        """
        match = re.search(r"^(.+?)\s*\(([^)]+)\)$", name.strip())
        if not match:
            return name
        before, inside = match.group(1), match.group(2)
        # If inside parens is mostly ASCII → it's the English name
        if all(ord(c) < 256 for c in inside.replace(" ", "")):
            return inside
        return before

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
