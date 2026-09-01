"""
LookupBarcodeQueryHandler - Handle barcode lookup with cascade:
DB -> FatSecret -> OpenFoodFacts -> USDA FDC -> Brave identity/estimate -> AI estimate.
"""

import asyncio
import logging
import time
from typing import Any

from src.api.exceptions import ExternalServiceException
from src.app.events.base import EventHandler, handles
from src.app.queries.food.lookup_barcode_query import LookupBarcodeQuery
from src.app.services.food_name_localizer import (
    needs_display_localization,
    translate_food_texts,
    translated_values,
)
from src.app.services.serving_label_localizer import localize_item_servings
from src.domain.model.translation_result import TranslationOutcome
from src.domain.services.barcode.barcode_logging import redact_barcode
from src.domain.services.barcode.barcode_nutrition_validator import (
    validate_barcode_nutrition,
)
from src.domain.services.prompts.system_prompts import SystemPrompts
from src.infra.adapters.fat_secret_service import FatSecretService
from src.infra.adapters.open_food_facts_service import OpenFoodFactsService

logger = logging.getLogger(__name__)

TRUSTED_CACHE_SOURCES = {"fatsecret", "openfoodfacts", "usda_fdc", "cache", None}
ENGLISH_CANONICAL_CACHE_SOURCES = frozenset({"fatsecret", "usda_fdc"})


def _gs1_prefix_hint(barcode: str) -> str:
    return barcode[:3] if len(barcode) >= 3 else "unknown"


@handles(LookupBarcodeQuery)
class LookupBarcodeQueryHandler(
    EventHandler[LookupBarcodeQuery, dict[str, Any] | None]
):
    """Handler for looking up product by barcode with fallback providers."""

    def __init__(
        self,
        open_food_facts_service: OpenFoodFactsService,
        fat_secret_service: FatSecretService | None,
        food_reference_repository: Any | None = None,
        async_uow_factory: Any | None = None,
        translation_service: Any | None = None,
        brave_search_service: Any | None = None,
        meal_generation_service: Any | None = None,
        macro_validation_service: Any | None = None,
        food_data_service: Any | None = None,
        food_mapping_service: Any | None = None,
        request_timeout_seconds: float | None = None,
        hedge_delay_seconds: float | None = None,
    ):
        self.off = open_food_facts_service
        self.fat_secret = fat_secret_service
        self.repo = food_reference_repository
        self.async_uow_factory = async_uow_factory
        self.translation_service = translation_service
        self.brave_search = brave_search_service
        self.meal_gen = meal_generation_service
        self.macro_validator = macro_validation_service
        self.food_data_service = food_data_service
        self.food_mapping_service = food_mapping_service
        self.request_timeout_seconds = (
            request_timeout_seconds if request_timeout_seconds is not None else 8.0
        )
        self.hedge_delay_seconds = (
            hedge_delay_seconds if hedge_delay_seconds is not None else 0.8
        )

    async def handle(self, query: LookupBarcodeQuery) -> dict[str, Any] | None:
        """Look up product by barcode with provider cascade and request deadline."""
        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                return await self._handle_internal(query)
        except TimeoutError:
            logger.warning(
                "[BARCODE-TIMEOUT] barcode_ref=%s deadline_seconds=%.2f",
                redact_barcode(query.scanned_barcode or query.barcode),
                self.request_timeout_seconds,
            )
            raise ExternalServiceException(
                "Barcode lookup timed out. Please try again.",
                error_code="BARCODE_LOOKUP_TIMEOUT",
            ) from None

    async def _handle_internal(
        self, query: LookupBarcodeQuery
    ) -> dict[str, Any] | None:
        """Internal lookup handler executing within request deadline."""
        started_at = time.perf_counter()
        miss_reasons: list[str] = []
        scanned_barcode = query.scanned_barcode or query.barcode
        barcode_ref = redact_barcode(scanned_barcode)
        aliases = query.aliases or (query.barcode,)

        def elapsed_ms() -> int:
            return round((time.perf_counter() - started_at) * 1000)

        def log_hit(source: str, result: dict[str, Any]) -> None:
            logger.info(
                "Barcode lookup hit barcode_ref=%s source=%s provider_source=%s "
                "elapsed_ms=%d name_present=%s is_estimate=%s",
                barcode_ref,
                source,
                source,
                elapsed_ms(),
                bool(result.get("name")),
                bool(result.get("is_estimate")),
            )

        partial_name: str | None = None

        cached = await self._get_cached_product(aliases)
        if (
            cached
            and self._has_nutrition(cached)
            and self._is_trusted_cached_row(cached)
        ):
            cached_product = self._canonicalize_cached_product(cached)
            if cached_product is not None:
                provider_source = cached_product.get("source")
                cached_product["provider_source"] = provider_source
                cached_product["source"] = "cache"
                cached_product["barcode"] = scanned_barcode
                log_hit("cache", cached_product)
                # FatSecret/USDA rows are acquired in English. Other cached rows may
                # lack provenance, so only localize leftover English display text.
                cache_source_language = (
                    "en" if provider_source in ENGLISH_CANONICAL_CACHE_SOURCES else None
                )
                return await self._maybe_translate(
                    cached_product,
                    query.language,
                    source_language=cache_source_language,
                )
            miss_reasons.append("cache_missing_source_identity")
        if cached:
            partial_name = cached.get("name")
            reason = (
                "cache_untrusted_source"
                if self._has_nutrition(cached)
                else "cache_partial_no_nutrition"
            )
            miss_reasons.append(reason)
            logger.debug(
                "[BARCODE-CASCADE] barcode_ref=%s step=cache reason=%s",
                barcode_ref,
                reason,
            )
        else:
            miss_reasons.append("cache_empty")

        # Hedged trusted provider lookup (FatSecret primary with OFF/USDA hedging)
        (
            trusted_result,
            provider_name,
            source_lang,
            provider_partial_name,
        ) = await self._lookup_trusted_providers(
            aliases, query.barcode, scanned_barcode, miss_reasons
        )
        if provider_partial_name:
            partial_name = partial_name or provider_partial_name
        if trusted_result is not None and provider_name is not None:
            cached_reference = await self._cache_result(
                trusted_result, cache_barcode=query.barcode
            )
            trusted_result = self._resolve_verified_identity(
                trusted_result, cached_reference, provider_source=provider_name
            )
            log_hit(provider_name, trusted_result)
            return await self._maybe_translate(
                trusted_result, query.language, source_language=source_lang
            )

        region = "US"
        brave_name: str | None = None
        brave_result: dict[str, Any] | None = None
        if self.brave_search:
            brave_result = await self.brave_search.get_product(
                scanned_barcode,
                query.language,
                product_name=partial_name,
            )
            if brave_result:
                brave_name = brave_result.get("name")
                partial_name = partial_name or brave_name
                if not self._has_nutrition(brave_result):
                    miss_reasons.append("brave_partial_no_nutrition")
        else:
            miss_reasons.append("brave_not_configured")

        if brave_name:
            estimate = await self._fatsecret_name_estimate(
                brave_name, region, query.language, scanned_barcode, barcode_ref
            )
            if estimate:
                estimate = await self._materialize_estimate_identity(
                    estimate,
                    cache_barcode=query.barcode,
                )
                log_hit("fatsecret_name_estimate", estimate)
                return await self._maybe_translate(estimate, query.language)

        if brave_result and self._has_nutrition(brave_result):
            estimate = self._estimate_result(
                brave_result, scanned_barcode, "brave_search"
            )
            estimate = await self._materialize_estimate_identity(
                estimate,
                cache_barcode=query.barcode,
            )
            log_hit("brave_search", estimate)
            return await self._maybe_translate(estimate, query.language)
        if self.brave_search and not brave_result:
            miss_reasons.append("brave_empty")

        estimate = await self._ai_estimate(
            scanned_barcode, query.language, partial_name
        )
        if estimate:
            estimate = await self._materialize_estimate_identity(
                estimate,
                cache_barcode=query.barcode,
            )
            log_hit("ai_estimate", estimate)
            return await self._maybe_translate(estimate, query.language)

        miss_reasons.append("ai_estimate_empty")
        logger.warning(
            "Barcode lookup miss barcode_ref=%s language=%s elapsed_ms=%d "
            "miss_reasons=%s partial_name_present=%s",
            barcode_ref,
            query.language,
            elapsed_ms(),
            ",".join(miss_reasons),
            bool(partial_name),
        )
        return None

    @staticmethod
    def _has_nutrition(result: dict[str, Any]) -> bool:
        """Check if result has meaningful macro data (not all zeros/None)."""
        for key in ("protein_100g", "carbs_100g", "fat_100g"):
            val = result.get(key)
            if val is not None and val > 0:
                return True
        return False

    @staticmethod
    def _has_name(result: dict[str, Any]) -> bool:
        """Check if result has a non-empty product name."""
        return bool(result.get("name"))

    @staticmethod
    def _is_trusted_cached_row(result: dict[str, Any]) -> bool:
        if (
            result.get("is_estimate")
            or result.get("source_namespace") == "ai_estimate"
            or result.get("source") == "ai_estimate"
        ):
            return False
        return (
            bool(result.get("is_verified"))
            or result.get("source") in TRUSTED_CACHE_SOURCES
        )

    @staticmethod
    def _canonicalize_cached_product(
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Attach a durable identity before returning a cached barcode row."""
        cached = dict(result)
        is_est = (
            cached.get("is_estimate")
            or cached.get("source_namespace") == "ai_estimate"
            or cached.get("source") == "ai_estimate"
        )
        cached["is_estimate"] = bool(is_est)
        reference_id = cached.get("id") or cached.get("food_reference_id")
        if reference_id is not None:
            try:
                reference_id = int(reference_id)
            except (TypeError, ValueError):
                reference_id = None
        if reference_id is not None:
            cached.update(
                {
                    "food_reference_id": reference_id,
                    "origin": "local",
                    "source_namespace": "food_reference",
                    "source_food_id": str(reference_id),
                }
            )
            return cached

        if cached.get("source_namespace") and cached.get("source_food_id"):
            cached["origin"] = "provider"
            return cached
        return None

    @staticmethod
    def _attach_cached_identity(
        result: dict[str, Any], cached_reference: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Prefer the durable local identity created by a verified cache write."""
        if cached_reference is None:
            return result
        canonical = LookupBarcodeQueryHandler._canonicalize_cached_product(
            cached_reference
        )
        if canonical is None or canonical.get("food_reference_id") is None:
            return result
        enriched = dict(canonical)
        for field in (
            "barcode",
            "source",
            "provider_source",
            "is_estimate",
            "calories_100g",
            "calories_per_100g",
            "nutrition_basis",
            "nutrition_contract_version",
        ):
            if field in result:
                enriched[field] = result[field]
        enriched.update(
            {
                "food_reference_id": canonical["food_reference_id"],
                "origin": "local",
                "source_namespace": "food_reference",
                "source_food_id": canonical["source_food_id"],
            }
        )
        return enriched

    @classmethod
    def _resolve_verified_identity(
        cls,
        result: dict[str, Any],
        cached_reference: dict[str, Any] | None,
        *,
        provider_source: str,
    ) -> dict[str, Any]:
        """Return an identity the meal-creation contract can resolve."""
        enriched = cls._attach_cached_identity(result, cached_reference)
        if enriched.get("food_reference_id") is not None:
            return enriched

        # FatSecret has a provider-backed resolver in the meal path. OFF and
        # USDA identities must be materialized locally before they are shown as
        # verified barcode results, otherwise the client cannot create a meal.
        if (
            provider_source == "fatsecret"
            and enriched.get("origin") == "provider"
            and enriched.get("source_namespace")
            and enriched.get("source_food_id")
        ):
            return enriched

        raise ExternalServiceException(
            "Barcode nutrition identity is temporarily unavailable. Please retry.",
            error_code="BARCODE_SOURCE_IDENTITY_UNAVAILABLE",
        )

    async def _materialize_estimate_identity(
        self,
        result: dict[str, Any],
        *,
        cache_barcode: str,
    ) -> dict[str, Any]:
        """Create the local identity required by released barcode clients."""
        payload = dict(result)
        payload.update(
            {
                "is_verified": False,
                "is_estimate": True,
                "source_namespace": "ai_estimate",
                "source_food_id": cache_barcode,
            }
        )
        cached_reference = await self._cache_result(
            payload,
            cache_barcode=cache_barcode,
        )
        resolved = self._resolve_verified_identity(
            payload,
            cached_reference,
            provider_source=str(payload["source"]),
        )
        resolved["is_estimate"] = True
        return resolved

    async def _first_barcode_hit(self, aliases, fetch):
        for alias in aliases:
            result = await fetch(alias)
            if result:
                return result
        return None

    async def _get_fdc_product(
        self,
        aliases: tuple[str, ...],
        canonical_barcode: str,
        scanned_barcode: str,
    ) -> dict[str, Any] | None:
        if not self.food_data_service or not self.food_mapping_service:
            return None
        try:
            raw = await self.food_data_service.get_branded_food_by_gtin(list(aliases))
        except Exception as exc:
            logger.warning("USDA FDC barcode lookup failed: %s", type(exc).__name__)
            return None
        if not raw:
            return None
        mapped = self.food_mapping_service.map_fdc_barcode_product(
            raw, barcode=canonical_barcode
        )
        mapped["barcode"] = scanned_barcode
        return validate_barcode_nutrition(mapped)

    async def _fatsecret_name_estimate(
        self,
        brave_name: str,
        region: str,
        language: str,
        scanned_barcode: str,
        barcode_ref: str,
    ) -> dict[str, Any] | None:
        if self.fat_secret is None:
            return None
        try:
            fs_results = await self.fat_secret.search_foods(
                brave_name,
                max_results=3,
                region=region,
                language=language,
            )
        except Exception as exc:
            logger.warning(
                "FatSecret name estimate failed barcode_ref=%s error=%s",
                barcode_ref,
                type(exc).__name__,
            )
            return None

        for item in fs_results or []:
            if self._has_nutrition(item):
                item["name"] = brave_name or item.get("name")
                return self._estimate_result(
                    item, scanned_barcode, "fatsecret_name_search"
                )
        return None

    def _trusted_provider_result(
        self,
        result: dict[str, Any],
        canonical_barcode: str,
        scanned_barcode: str,
        source: str,
    ) -> dict[str, Any]:
        payload = validate_barcode_nutrition(result)
        payload["barcode"] = scanned_barcode
        payload["cache_barcode"] = canonical_barcode
        payload["source"] = source
        payload["provider_source"] = source
        payload["is_verified"] = True
        payload["is_estimate"] = False
        return payload

    @staticmethod
    def _estimate_result(
        result: dict[str, Any],
        scanned_barcode: str,
        source: str,
    ) -> dict[str, Any]:
        payload = validate_barcode_nutrition(result)
        payload["barcode"] = scanned_barcode
        payload["source"] = source
        payload["provider_source"] = source
        payload["is_estimate"] = True
        payload["is_verified"] = False
        return payload

    async def _ai_estimate(
        self,
        barcode: str,
        language: str,
        partial_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Estimate nutrition via AI when all other sources fail."""
        if not self.meal_gen:
            return None
        try:
            system_prompt = SystemPrompts.BARCODE_AI_ESTIMATE
            name_hint = f"Product name: {partial_name}\n" if partial_name else ""
            user_prompt = (
                f"{name_hint}"
                f"Barcode: {barcode}\n"
                f"GS1 prefix allocation hint: {_gs1_prefix_hint(barcode)}\n"
                f"Language: {language}"
            )
            result = await self.meal_gen.generate_meal_plan_async(
                user_prompt,
                system_prompt,
                response_type="json",
                max_tokens=500,
                model_purpose="barcode",
            )
            if not result or not isinstance(result, dict):
                return None

            if not result.get("is_food", True):
                logger.debug(
                    "Non-food item detected for barcode_ref=%s", redact_barcode(barcode)
                )
                return None

            payload = self._estimate_result(result, barcode, "ai_estimate")
            if not payload.get("name"):
                payload["name"] = partial_name or "Unknown product"
            return payload
        except Exception as exc:
            logger.warning(
                "AI estimation failed for barcode_ref=%s error=%s",
                redact_barcode(barcode),
                type(exc).__name__,
            )
            return None

    async def _maybe_translate(
        self,
        result: dict[str, Any],
        language: str,
        source_language: str | None = None,
    ) -> dict[str, Any]:
        """Translate known-English or leftover-English names for presentation."""
        name = result.get("name")
        if language == "en":
            return result
        if not name:
            return await self._localize_result_servings(result, language)

        # Proven English sources always localize. Unknown provenance only
        # localizes when the display name still looks like leftover English,
        # so printed non-English product names are not mangled.
        localized = dict(result)
        if source_language == "en" or (
            source_language is None and needs_display_localization(str(name), language)
        ):
            translated = await translate_food_texts(
                [str(name)],
                target_language=language,
                translation_service=self.translation_service,
            )
            if translated.outcome in {
                TranslationOutcome.TRANSLATED,
                TranslationOutcome.PARTIAL,
            }:
                localized["name"] = translated_values([str(name)], translated)[0]
        return await self._localize_result_servings(localized, language)

    async def _localize_result_servings(
        self, result: dict[str, Any], language: str
    ) -> dict[str, Any]:
        if not result.get("allowed_units"):
            return result
        batch = [dict(result)]
        await localize_item_servings(
            batch,
            language=language,
            translation_service=self.translation_service,
            uow_factory=self.async_uow_factory,
            persist=True,
        )
        return batch[0]

    async def _get_cached_product(
        self, aliases: tuple[str, ...]
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for barcode in aliases:
            cached = await self._get_cached_product_by_barcode(barcode)
            if cached:
                candidates.append(cached)
        if not candidates:
            return None
        candidates.sort(
            key=lambda row: (
                not bool(row.get("is_verified")),
                row.get("source") not in TRUSTED_CACHE_SOURCES,
            )
        )
        return candidates[0]

    async def _get_cached_product_by_barcode(
        self, barcode: str
    ) -> dict[str, Any] | None:
        if self.async_uow_factory is not None:
            async with self.async_uow_factory() as uow:
                return await uow.food_references.get_by_barcode(barcode)
        if self.repo is None:
            return None
        cached = self.repo.get_by_barcode(barcode)
        if hasattr(cached, "__await__"):
            return await cached
        return cached

    async def _cache_result(
        self, result: dict[str, Any], cache_barcode: str | None = None
    ) -> dict[str, Any] | None:
        """Cache a verified result and return its durable reference row."""
        if not result.get("name"):
            logger.warning(
                "Skipping barcode cache: name is required",
            )
            return None
        payload = dict(result)
        if cache_barcode:
            payload["barcode"] = cache_barcode
        payload.pop("cache_barcode", None)
        lookup_barcode = payload.get("barcode")
        try:
            if self.async_uow_factory is not None:
                async with self.async_uow_factory() as uow:
                    await uow.food_references.upsert(payload)
                    if lookup_barcode:
                        return await uow.food_references.get_by_barcode(lookup_barcode)
                return None
            if self.repo is None:
                return None
            upserted = self.repo.upsert(payload)
            if hasattr(upserted, "__await__"):
                await upserted
            if lookup_barcode:
                get_by_barcode = getattr(self.repo, "get_by_barcode", None)
                if get_by_barcode is not None:
                    cached = get_by_barcode(lookup_barcode)
                    if hasattr(cached, "__await__"):
                        return await cached
                    return cached
            return None
        except Exception as exc:
            logger.warning(
                "Failed to cache barcode result error=%s",
                type(exc).__name__,
            )
            return None

    async def _lookup_trusted_providers(
        self,
        aliases: tuple[str, ...],
        query_barcode: str,
        scanned_barcode: str,
        miss_reasons: list[str],
    ) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
        """Lookup barcode across trusted providers with FatSecret-first hedging."""
        fs_task: asyncio.Task[Any] | None = None
        off_task: asyncio.Task[Any] | None = None
        fdc_task: asyncio.Task[Any] | None = None
        all_tasks: list[asyncio.Task[Any]] = []
        fs_result: dict[str, Any] | None = None
        off_result: dict[str, Any] | None = None
        fdc_result: dict[str, Any] | None = None
        partial_name: str | None = None

        def _remember_partial(result: dict[str, Any] | None) -> None:
            nonlocal partial_name
            if partial_name or not result:
                return
            name = result.get("name")
            if name:
                partial_name = str(name)

        try:
            if self.fat_secret is not None:
                fs_task = asyncio.create_task(
                    self._first_barcode_hit(
                        aliases,
                        lambda alias: self.fat_secret.get_product(
                            alias,
                            region="US",
                            language="en",
                        ),
                    )
                )
                all_tasks.append(fs_task)

            if fs_task is not None:
                done, _ = await asyncio.wait(
                    {fs_task}, timeout=self.hedge_delay_seconds
                )
                if fs_task in done:
                    try:
                        fs_result = fs_task.result()
                        _remember_partial(fs_result)
                        if (
                            fs_result
                            and self._has_nutrition(fs_result)
                            and self._has_name(fs_result)
                        ):
                            result = self._trusted_provider_result(
                                fs_result,
                                query_barcode,
                                scanned_barcode,
                                "fatsecret",
                            )
                            return result, "fatsecret", "en", None
                    except Exception as exc:
                        logger.warning("[BARCODE-FATSECRET-FAIL] %s", exc)
                        fs_result = None

            if self.off is not None:
                off_task = asyncio.create_task(
                    self._first_barcode_hit(aliases, self.off.get_product)
                )
                all_tasks.append(off_task)
            if self.food_data_service is not None:
                fdc_task = asyncio.create_task(
                    self._get_fdc_product(aliases, query_barcode, scanned_barcode)
                )
                all_tasks.append(fdc_task)

            pending_tasks = [t for t in all_tasks if not t.done()]
            while pending_tasks:
                done, _ = await asyncio.wait(
                    pending_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    pending_tasks.remove(t)

                if fs_task is not None and fs_task.done() and not fs_task.cancelled():
                    try:
                        fs_result = fs_task.result()
                        _remember_partial(fs_result)
                        if (
                            fs_result
                            and self._has_nutrition(fs_result)
                            and self._has_name(fs_result)
                        ):
                            for pending in pending_tasks:
                                pending.cancel()
                            result = self._trusted_provider_result(
                                fs_result, query_barcode, scanned_barcode, "fatsecret"
                            )
                            return result, "fatsecret", "en", None
                    except Exception as exc:
                        logger.warning("[BARCODE-FATSECRET-FAIL] %s", exc)
                        fs_result = None

                if (
                    off_task is not None
                    and off_task.done()
                    and not off_task.cancelled()
                ):
                    try:
                        off_result = off_task.result()
                        _remember_partial(off_result)
                        if (
                            off_result
                            and self._has_nutrition(off_result)
                            and self._has_name(off_result)
                        ):
                            for pending in pending_tasks:
                                pending.cancel()
                            res_dict = dict(off_result)
                            source_language = res_dict.pop("source_language", None)
                            result = self._trusted_provider_result(
                                res_dict,
                                query_barcode,
                                scanned_barcode,
                                "openfoodfacts",
                            )
                            return result, "openfoodfacts", source_language, None
                    except Exception as exc:
                        logger.warning("[BARCODE-OPENFOODFACTS-FAIL] %s", exc)
                        off_result = None

                if (
                    fdc_task is not None
                    and fdc_task.done()
                    and not fdc_task.cancelled()
                ):
                    try:
                        fdc_result = fdc_task.result()
                        _remember_partial(fdc_result)
                        if (
                            fdc_result
                            and self._has_nutrition(fdc_result)
                            and self._has_name(fdc_result)
                        ):
                            for pending in pending_tasks:
                                pending.cancel()
                            return fdc_result, "usda_fdc", "en", None
                    except Exception as exc:
                        logger.warning("[BARCODE-USDA-FDC-FAIL] %s", exc)
                        fdc_result = None

            self._record_trusted_provider_misses(
                miss_reasons,
                fat_secret_configured=self.fat_secret is not None,
                fat_secret_result=fs_result,
                off_result=off_result,
                fdc_result=fdc_result,
            )
            return None, None, None, partial_name
        finally:
            for t in all_tasks:
                if not t.done():
                    t.cancel()
            if all_tasks:
                await asyncio.gather(*all_tasks, return_exceptions=True)

    def _record_trusted_provider_misses(
        self,
        miss_reasons: list[str],
        *,
        fat_secret_configured: bool,
        fat_secret_result: dict[str, Any] | None,
        off_result: dict[str, Any] | None,
        fdc_result: dict[str, Any] | None,
    ) -> None:
        """Restore per-provider miss diagnostics after a hedged trusted-lookup miss."""
        if not fat_secret_configured:
            miss_reasons.append("fatsecret_not_configured")
        elif fat_secret_result:
            miss_reasons.append(
                "fatsecret_partial_no_nutrition"
                if not self._has_nutrition(fat_secret_result)
                else "fatsecret_partial_no_name"
            )
        else:
            miss_reasons.append("fatsecret_empty")

        if off_result:
            miss_reasons.append(
                "openfoodfacts_partial_no_nutrition"
                if not self._has_nutrition(off_result)
                else "openfoodfacts_partial_no_name"
            )
        else:
            miss_reasons.append("openfoodfacts_empty")

        if fdc_result:
            miss_reasons.append(
                "usda_fdc_partial_no_name"
                if self._has_nutrition(fdc_result)
                else "usda_fdc_partial_no_nutrition"
            )
        else:
            miss_reasons.append("usda_fdc_empty")
