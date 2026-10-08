"""Search foods for manual logging: local catalog first, provider in the background."""

import copy
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from inspect import isawaitable
from time import perf_counter
from typing import Any

from src.app.events.base import EventHandler, handles
from src.app.queries.food.search_foods_query import SearchFoodsQuery
from src.app.services.food_display_name import leftover_display_names
from src.app.services.food_name_localizer import translate_food_texts
from src.app.services.food_search_background_completion import (
    CompletionStatus,
    CompletionWork,
    FoodSearchBackgroundCompletions,
    Publish,
    SearchResults,
    completion_budget_seconds,
)
from src.app.services.food_search_cache_key import food_search_cache_key
from src.app.services.food_search_local_tiers import (
    local_results_are_sufficient,
    merge_local_tiers,
    split_strong_matches,
)
from src.app.services.food_search_provider_adoption import adopt_provider_hits
from src.app.services.food_search_stage_timings import FoodSearchStageTimings
from src.app.services.search_result_localizer import localize_search_result_names
from src.app.services.serving_label import leftover_serving_phrases
from src.app.services.serving_label_localizer import (
    localize_item_servings_deferred,
    persist_item_serving_labels,
)
from src.domain.model.translation_result import TranslationOutcome
from src.domain.ports.food_mapping_service_port import FoodMappingServicePort
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceSearchProjection,
)
from src.domain.services.nutrition_integrity_policy import NutritionIntegrityError
from src.domain.utils.food_search_text import clip_food_search_query
from src.observability import distribution_metric, increment_metric

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SearchRun:
    """One request's search shape plus what its response needs to report."""

    event: SearchFoodsQuery
    mode: str
    cache_key: str
    cache_context: Mapping[str, int | str] | None
    timings: FoodSearchStageTimings
    started: float


@handles(SearchFoodsQuery)
class SearchFoodsQueryHandler(EventHandler[SearchFoodsQuery, dict[str, Any]]):
    """Answer from the local catalog; let FatSecret fill gaps within a budget.

    Provider work that misses the wait budget keeps running so its page still
    reaches the cache, and an identical search joins it instead of calling
    FatSecret again.
    """

    def __init__(
        self,
        cache_service,
        mapping_service: FoodMappingServicePort,
        fat_secret_service: Any | None = None,
        translation_service: Any | None = None,
        local_search: Callable[[str, str, int], Any] | None = None,
        integrity_context: Callable[[], Any] | None = None,
        uow_factory: Any | None = None,
        background: FoodSearchBackgroundCompletions | None = None,
    ):
        self.cache_service = cache_service
        self.mapping_service = mapping_service
        self.fat_secret_service = fat_secret_service
        self.translation_service = translation_service
        self.local_search = local_search
        self.integrity_context = integrity_context
        # Write access for catalog adoption. This handler is a process-global
        # singleton, so it must not hold an open session; every write opens a
        # fresh UoW through this factory.
        self.uow_factory = uow_factory
        # Provider work outlives the request that started it; one owner
        # dedupes identical searches and drains the work at shutdown.
        self.background = (
            background if background is not None else FoodSearchBackgroundCompletions()
        )

    async def drain(self, timeout: float = 5.0) -> None:
        """Let background search work finish (shutdown, tests)."""
        await self.background.drain(timeout)

    async def handle(self, event: SearchFoodsQuery) -> dict[str, Any]:
        started = perf_counter()
        # Pasted text is searched by its start rather than rejected: the app
        # has no input cap, and a rejection would show an error retry can't fix.
        event = replace(event, query=clip_food_search_query(event.query))
        if not event.query or not event.query.strip():
            self._record_search_metrics(
                started,
                source="local",
                language=event.language,
                status="empty",
            )
            return {"results": [], "query": event.query, "total": 0}

        mode = "autocomplete" if event.autocomplete else "search"
        timings = FoodSearchStageTimings()
        with timings.measure("cache_read"):
            cache_context = await self._get_integrity_context()
            run = _SearchRun(
                event=event,
                mode=mode,
                cache_key=self._cache_key(
                    event.query, event.language, mode=mode, limit=event.limit
                ),
                cache_context=cache_context,
                timings=timings,
                started=started,
            )
            cached = await self._read_cached_results(run)
        if cached is not None:
            mapped = self._map_search_items(cached)
            return self._respond(
                run,
                mapped,
                source="cache",
                status="success" if mapped else "empty",
            )
        if event.language == "en":
            return await self._search_english(run)
        return await self._search_non_english(run)

    async def _read_cached_results(self, run: _SearchRun) -> SearchResults | None:
        context = run.cache_context
        # Integrity-controlled entries are versioned by policy and generation;
        # without that context no entry can be trusted.
        if self.integrity_context is not None and context is None:
            return None
        try:
            if context is None:
                cached = await self.cache_service.get_cached_search(run.cache_key)
            else:
                cached = await self.cache_service.get_cached_search(
                    run.cache_key,
                    policy_version=str(context["policy_version"]),
                    generation=int(context["generation"]),
                )
        except Exception:
            logger.warning("food search cache read failed", exc_info=True)
            return None
        if not cached:
            return None
        language = run.event.language
        processed = self._process_search_results(cached, capitalize=language == "en")[
            : run.event.limit
        ]
        if language != "en" and (
            leftover_display_names(
                [
                    {
                        **item,
                        "name": str(item.get("description") or item.get("name") or ""),
                    }
                    for item in processed
                ],
                language,
            )
            or self._has_leftover_units(processed, language)
        ):
            return None
        for item in processed:
            item.setdefault("source", "fatsecret")
        return processed

    async def _search_english(self, run: _SearchRun) -> dict[str, Any]:
        event = run.event
        with run.timings.measure("local"):
            local_raw, local_ok = await self._search_local(event)
        if self.fat_secret_service is None or local_results_are_sufficient(
            local_raw, event.limit, event.query
        ):
            return self._respond_with(
                run, local_raw, partial=not local_ok, degraded=not local_ok
            )
        # The work may finish after this response; it gets rows of its own.
        strong, weak = copy.deepcopy(split_strong_matches(local_raw, event.query))

        async def work(publish: Publish) -> None:
            timings = FoodSearchStageTimings()
            try:
                with timings.measure("provider"):
                    try:
                        # Candidates only — never fan out food.get.v5 per hit.
                        # Detail enrichment happens on provider details / select.
                        provider = await self._search_provider_candidates(
                            event.query, max(event.limit - len(strong), 1)
                        )
                    except Exception:
                        logger.warning("fatsecret search failed", exc_info=True)
                        return
                merged = self._merge_search_results(
                    strong, provider, event.limit, trailing_local=weak
                )
                publish(merged, partial=not local_ok)
                if not event.autocomplete:
                    with timings.measure("adopt"):
                        await adopt_provider_hits(
                            merged, locale="en", uow_factory=self.uow_factory
                        )
                if provider and merged and local_ok:
                    with timings.measure("cache_write"):
                        await self._cache_search(
                            run.cache_key, merged, run.cache_context
                        )
            finally:
                timings.emit(language=event.language, mode=run.mode)

        return await self._wait_for_provider(run, work, local_raw)

    async def _search_non_english(self, run: _SearchRun) -> dict[str, Any]:
        event = run.event
        language = event.language
        with run.timings.measure("local"):
            native, native_ok = await self._search_local(event)
        if local_results_are_sufficient(native, event.limit, event.query):
            with run.timings.measure("present"):
                presented, complete = await self._present_with_glossary(
                    native, language
                )
            partial = not complete and self.translation_service is not None
            if partial:
                self.background.start(
                    run.cache_key,
                    self._localization_work(run, copy.deepcopy(native)),
                    name=f"food-search-{run.mode}",
                )
            return self._respond_with(run, presented, partial=partial)
        native_rows = copy.deepcopy(native)

        async def work(publish: Publish) -> None:
            timings = FoodSearchStageTimings()
            try:
                with timings.measure("canonical"):
                    canonical, canonical_complete = await self._canonical_query(
                        event.query, language
                    )
                translated: SearchResults = []
                translated_ok = True
                if canonical.strip().lower() != event.query.strip().lower():
                    with timings.measure("local_translated"):
                        translated, translated_ok = await self._search_local(
                            event, query=canonical, region="US"
                        )
                queries = (event.query, canonical)
                merged = merge_local_tiers(
                    native_rows,
                    translated,
                    limit=event.limit,
                    key=self._search_result_key,
                    queries=queries,
                )
                provider_failed = False
                if self.fat_secret_service is not None and not (
                    local_results_are_sufficient(merged, event.limit, *queries)
                ):
                    strong, weak = split_strong_matches(merged, *queries)
                    provider: SearchResults = []
                    with timings.measure("provider"):
                        try:
                            provider = await self._search_provider_candidates(
                                canonical, max(event.limit - len(strong), 1)
                            )
                        except Exception:
                            logger.warning(
                                "fatsecret canonical search failed", exc_info=True
                            )
                            provider_failed = True
                    for item in provider:
                        english = str(item.get("description") or item.get("name") or "")
                        if english:
                            item.setdefault("canonical_name", english)
                    merged = self._merge_search_results(
                        strong, provider, event.limit, trailing_local=weak
                    )
                with timings.measure("translate"):
                    localized, names_cacheable = await self._localize_results(
                        merged, language=language
                    )
                raw_units = [
                    copy.deepcopy(item.get("allowed_units")) for item in localized
                ]
                with timings.measure("servings"):
                    labels, persistable = await localize_item_servings_deferred(
                        localized,
                        language=language,
                        translation_service=self.translation_service,
                        uow_factory=self.uow_factory,
                    )
                # A source that failed may answer next time: serve this page,
                # but neither cache it nor present it as the whole answer.
                degraded = provider_failed or not (
                    native_ok and canonical_complete and translated_ok
                )
                publish(localized, partial=degraded)
                if not event.autocomplete:
                    with timings.measure("adopt"):
                        await self._adopt_with_raw_units(localized, raw_units, language)
                    if persistable:
                        with timings.measure("persist_labels"):
                            await persist_item_serving_labels(
                                localized,
                                labels,
                                language=language,
                                uow_factory=self.uow_factory,
                            )
                if (
                    names_cacheable
                    and localized
                    and not degraded
                    and not self._has_leftover_units(localized, language)
                ):
                    with timings.measure("cache_write"):
                        await self._cache_search(
                            run.cache_key, localized, run.cache_context
                        )
            finally:
                timings.emit(language=language, mode=run.mode)

        return await self._wait_for_provider(run, work, native)

    async def _wait_for_provider(
        self, run: _SearchRun, work: CompletionWork, local_raw: SearchResults
    ) -> dict[str, Any]:
        """Start (or join) ``work``; answer from local rows if it runs late."""
        event = run.event
        ready = self.background.start(
            run.cache_key, work, name=f"food-search-{run.mode}"
        )
        with run.timings.measure("wait"):
            outcome = await self.background.wait(
                ready,
                completion_budget_seconds(
                    autocomplete=event.autocomplete,
                    has_local_results=bool(local_raw),
                ),
            )
        if outcome.status is CompletionStatus.READY:
            return self._respond_with(
                run,
                outcome.results,
                degraded=outcome.partial,
                partial=outcome.partial,
            )
        fallback = local_raw
        if event.language != "en":
            with run.timings.measure("present"):
                fallback, _ = await self._present_with_glossary(
                    local_raw, event.language
                )
        return self._respond_with(
            run,
            fallback,
            partial=True,
            timed_out=outcome.status is CompletionStatus.TIMEOUT,
            degraded=outcome.status is CompletionStatus.FAILED,
        )

    def _localization_work(
        self, run: _SearchRun, items: SearchResults
    ) -> CompletionWork:
        """Translate what the glossary left in English, then cache the page."""
        event = run.event
        language = event.language

        async def work(publish: Publish) -> None:
            timings = FoodSearchStageTimings()
            try:
                with timings.measure("translate"):
                    localized, names_cacheable = await self._localize_results(
                        items, language=language
                    )
                with timings.measure("servings"):
                    labels, persistable = await localize_item_servings_deferred(
                        localized,
                        language=language,
                        translation_service=self.translation_service,
                        uow_factory=self.uow_factory,
                    )
                publish(localized)
                if persistable and not event.autocomplete:
                    with timings.measure("persist_labels"):
                        await persist_item_serving_labels(
                            localized,
                            labels,
                            language=language,
                            uow_factory=self.uow_factory,
                        )
                if (
                    names_cacheable
                    and localized
                    and not self._has_leftover_units(localized, language)
                ):
                    with timings.measure("cache_write"):
                        await self._cache_search(
                            run.cache_key, localized, run.cache_context
                        )
            finally:
                timings.emit(language=language, mode=run.mode)

        return work

    async def _present_with_glossary(
        self, items: SearchResults, language: str
    ) -> tuple[SearchResults, bool]:
        """Localize names and serving labels without the translator.

        Returns the presented rows and whether nothing is left in English.
        """
        presented, names_complete = await localize_search_result_names(
            items, language=language, translation_service=None
        )
        await localize_item_servings_deferred(
            presented,
            language=language,
            translation_service=None,
            uow_factory=self.uow_factory,
        )
        return presented, names_complete and not self._has_leftover_units(
            presented, language
        )

    async def _adopt_with_raw_units(
        self, items: SearchResults, raw_units: list[Any], language: str
    ) -> None:
        """Adopt provider hits with the serving units FatSecret returned.

        Catalog serving rows take their Vietnamese label from
        ``display_description``; labels are persisted separately, and only
        when the translation is trustworthy, so adoption sees the raw units.
        """
        candidates = [
            {**item, "allowed_units": units}
            for item, units in zip(items, raw_units, strict=True)
        ]
        await adopt_provider_hits(
            candidates, locale=language, uow_factory=self.uow_factory
        )
        for item, candidate in zip(items, candidates, strict=True):
            if candidate.get("food_reference_id") is not None:
                item["food_reference_id"] = candidate["food_reference_id"]

    def _respond(
        self,
        run: _SearchRun,
        mapped: list[dict[str, Any]],
        *,
        source: str,
        status: str,
        partial: bool = False,
    ) -> dict[str, Any]:
        self._record_search_metrics(
            run.started,
            source=source,
            language=run.event.language,
            status=status,
        )
        run.timings.emit(language=run.event.language, mode=run.mode)
        response: dict[str, Any] = {
            "results": mapped,
            "query": run.event.query,
            "total": len(mapped),
        }
        if partial:
            # Slower sources were late or failed; a refetch may return more.
            response["partial"] = True
        return response

    def _respond_with(
        self,
        run: _SearchRun,
        raw: SearchResults,
        *,
        partial: bool = False,
        degraded: bool = False,
        timed_out: bool = False,
    ) -> dict[str, Any]:
        mapped = self._map_search_items(raw)
        local_count = sum(1 for item in raw if item.get("source") == "food_reference")
        return self._respond(
            run,
            mapped,
            source=self._source_label(local_count, len(raw)),
            status=self._status_label(
                result_count=len(mapped),
                degraded=degraded,
                timed_out=timed_out,
            ),
            partial=partial,
        )

    def _map_search_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mapped: list[dict[str, Any]] = []
        for item in items:
            try:
                mapped_item = self.mapping_service.map_search_item(item)
            except NutritionIntegrityError as exc:
                logger.info(
                    "food search item rejected by nutrition integrity policy: %s",
                    exc.result.reason_code,
                )
                continue
            # The generic provider mapper does not know about adoption; carry
            # the catalog id through so newly-adopted FatSecret hits resolve
            # to the durable food_reference row instead of a thin provider id.
            if (
                item.get("food_reference_id") is not None
                and "food_reference_id" not in mapped_item
            ):
                mapped_item["food_reference_id"] = item["food_reference_id"]
            mapped.append(mapped_item)
        return mapped

    async def _search_provider_candidates(
        self, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """FatSecret search without per-hit detail fetches."""
        service = self.fat_secret_service
        if service is None:
            return []
        search_candidates = getattr(service, "search_food_candidates", None)
        if callable(search_candidates):
            result = search_candidates(query, max_results=limit)
            if isawaitable(result):
                resolved = await result
                if isinstance(resolved, list):
                    return resolved
            elif isinstance(result, list):
                return result
        # Test doubles / older adapters may only expose search_foods.
        return await service.search_foods(query, max_results=limit)

    async def _canonical_query(self, query: str, language: str) -> tuple[str, bool]:
        """English search text, and whether it is as good as this query gets.

        Only a translator outage is worth retrying. A word the translator keeps
        unchanged (French "pizza") is rejected the same way every time, so a
        page built from the typed text is still the answer for that query.
        """
        if language == "en" or self.translation_service is None:
            return query, True
        result = await translate_food_texts(
            [query],
            source_language=language,
            target_language="en",
            translation_service=self.translation_service,
        )
        if result.outcome is TranslationOutcome.TRANSLATED and result.texts:
            return result.texts[0], True
        return query, result.outcome is not TranslationOutcome.UNAVAILABLE

    async def _localize_results(
        self,
        results: list[dict[str, Any]],
        *,
        language: str,
    ) -> tuple[list[dict[str, Any]], bool]:
        localized, cacheable = await localize_search_result_names(
            results,
            language=language,
            translation_service=self.translation_service,
        )
        return localized, cacheable

    async def _cache_search(
        self,
        cache_key: str,
        results: list[dict[str, Any]],
        cache_context: Mapping[str, int | str] | None,
    ) -> None:
        if self.integrity_context is not None and cache_context is None:
            return
        try:
            if cache_context is None:
                await self.cache_service.cache_search(cache_key, results)
            else:
                await self.cache_service.cache_search(
                    cache_key,
                    results,
                    policy_version=str(cache_context["policy_version"]),
                    generation=int(cache_context["generation"]),
                )
        except Exception:
            logger.warning("food search cache write failed", exc_info=True)

    async def _get_integrity_context(self) -> Mapping[str, int | str] | None:
        if self.integrity_context is None:
            return None
        try:
            value = await self.integrity_context()
            if not isinstance(value, Mapping):
                raise TypeError("integrity context must be a mapping")
            if "policy_version" not in value or "generation" not in value:
                raise ValueError("integrity context is incomplete")
            return value
        except Exception:
            logger.warning("food integrity control read failed", exc_info=True)
            return None

    async def _search_local(
        self,
        event: SearchFoodsQuery,
        *,
        query: str | None = None,
        region: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Matching catalog rows, and whether the lookup itself succeeded.

        A failed lookup reads as no rows so the provider can still answer, but
        that page lacks the catalog's rows and must not be cached as the answer.
        """
        if not self.local_search:
            return [], True
        try:
            projections = await self.local_search(
                query if query is not None else event.query,
                region or self._region_for_language(event.language),
                event.limit,
            )
        except Exception:
            logger.warning("local food_reference search failed", exc_info=True)
            return [], False
        return [self._local_projection_to_raw(item) for item in projections], True

    def _local_projection_to_raw(
        self,
        item: FoodReferenceSearchProjection,
    ) -> dict[str, Any]:
        return {
            "source": "food_reference",
            "food_reference_id": item.id,
            "origin": "local",
            "source_namespace": item.source_namespace or "food_reference",
            "source_food_id": item.source_food_id or str(item.id),
            "food_id": f"food_reference:{item.id}",
            "description": item.name,
            "name": item.name,
            "name_vi": item.name_vi,
            "name_normalized": item.name_normalized,
            "brand": item.brand,
            "provider_source": item.source,
            "is_verified": item.is_verified,
            "serving_description": item.serving_size,
            "allowed_units": item.allowed_units,
            "protein_100g": item.protein_100g,
            "carbs_100g": item.carbs_100g,
            "fat_100g": item.fat_100g,
            "fiber_100g": item.fiber_100g,
            "sugar_100g": item.sugar_100g,
        }

    def _merge_search_results(
        self,
        local_raw: Sequence[dict[str, Any]],
        provider_raw: Sequence[dict[str, Any]],
        limit: int,
        *,
        trailing_local: Sequence[dict[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        """Local rows, then provider rows, then ``trailing_local`` rows.

        ``trailing_local`` holds weak local matches. They rank after the
        provider page, but a provider row for the same food is shown as the
        local row so its catalog id survives.
        """
        merged = list(local_raw)
        seen = {self._search_result_key(item) for item in merged}
        local_names = {
            str(item.get("name_normalized") or item.get("description") or "")
            .strip()
            .lower()
            for item in [*local_raw, *trailing_local]
        }
        trailing_by_key = {
            self._search_result_key(item): item for item in trailing_local
        }
        for item in provider_raw:
            if len(merged) >= limit:
                break
            item.setdefault("source", "fatsecret")
            if not item.get("source_food_id") and not item.get("food_id"):
                provider_name = (
                    str(item.get("name_normalized") or item.get("description") or "")
                    .strip()
                    .lower()
                )
                if provider_name in local_names:
                    continue
            key = self._search_result_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(trailing_by_key.get(key, item))
        for item in trailing_local:
            if len(merged) >= limit:
                break
            key = self._search_result_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged[:limit]

    def _search_result_key(self, item: dict[str, Any]) -> str:
        namespace = item.get("source_namespace")
        source_id = item.get("source_food_id")
        if namespace and source_id is not None:
            return f"identity:{str(namespace).strip().lower()}:{str(source_id).strip()}"
        if item.get("food_reference_id") is not None:
            return f"identity:food_reference:{item['food_reference_id']}"
        food_id = item.get("food_id")
        if food_id and ":" in str(food_id):
            return f"identity:{str(food_id).strip().lower()}"
        source = str(item.get("source") or "").strip().lower()
        if source in {"fatsecret", "openfoodfacts", "provider"} and food_id:
            return f"identity:{source}:{str(food_id).strip()}"
        normalized = item.get("name_normalized")
        if normalized:
            return str(normalized).strip().lower()
        return str(item.get("description") or item.get("name") or "").strip().lower()

    def _region_for_language(self, language: str) -> str:
        if language == "vi":
            return "VN"
        return "US"

    @staticmethod
    def _cache_key(query: str, language: str, *, mode: str, limit: int) -> str:
        return food_search_cache_key(query, language, mode=mode, limit=limit)

    @staticmethod
    def _has_leftover_units(items: SearchResults, language: str) -> bool:
        return any(
            leftover_serving_phrases(item.get("allowed_units") or [], language)
            for item in items
        )

    def _source_label(self, local_count: int, result_count: int) -> str:
        if local_count and result_count > local_count:
            return "mixed"
        if local_count:
            return "local"
        return "provider"

    @staticmethod
    def _status_label(
        *,
        result_count: int,
        degraded: bool = False,
        timed_out: bool = False,
    ) -> str:
        if timed_out:
            return "timeout"
        if degraded:
            return "degraded"
        if result_count:
            return "success"
        return "empty"

    def _record_search_metrics(
        self,
        started: float,
        *,
        source: str,
        language: str,
        status: str,
    ) -> None:
        attributes = {
            "operation": "search",
            "source": source,
            "language": language,
            "status": status,
        }
        distribution_metric(
            "food_search.operation.latency_ms",
            (perf_counter() - started) * 1000,
            unit="millisecond",
            attributes=attributes,
        )
        increment_metric("food_search.requests", attributes=attributes)

    def _process_search_results(
        self, raw_results: list[dict[str, Any]], *, capitalize: bool = True
    ) -> list[dict[str, Any]]:
        """Deduplicate results and, unless told otherwise, title-case names."""
        if not raw_results:
            return raw_results

        seen_names = set()
        processed_results = []

        for item in raw_results:
            original_name = item.get("description", "")
            name = (
                self._capitalize_food_name(original_name)
                if capitalize
                else original_name
            )
            name_key = self._search_result_key({**item, "description": name})

            if name_key not in seen_names:
                seen_names.add(name_key)
                processed_item = item.copy()
                processed_item["description"] = name
                processed_results.append(processed_item)

        return processed_results

    def _capitalize_food_name(self, name: str) -> str:
        """Properly capitalize food names."""
        if not name:
            return name

        parts = []
        for part in name.split(","):
            words = []
            for word in part.strip().split():
                word_lower = word.lower()
                if word_lower in [
                    "and",
                    "or",
                    "with",
                    "in",
                    "on",
                    "of",
                    "the",
                    "a",
                    "an",
                ]:
                    words.append(word_lower if words else word.capitalize())
                else:
                    words.append(word.capitalize())

            if words:
                parts.append(" ".join(words))

        return ", ".join(parts)
