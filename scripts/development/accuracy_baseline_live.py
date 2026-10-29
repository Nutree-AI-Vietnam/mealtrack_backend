"""Live provider run for the QA-labeled accuracy baseline.

Offline mode does not import this module's provider wiring. Live mode calls
``VisionAIService.analyze`` and ``ParseMealTextHandler`` the way the product
does, then optionally ``FoodReferenceValidationService.ground_meal``.

``AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED`` is not written. The ON arm calls
``ground_meal`` directly, which is the path that flag selects in production.
The OFF arm never calls it. Both scan arms share one vision response so the
comparison is the grounding delta. Parse-text does not read that flag.
"""

from __future__ import annotations

import asyncio
import copy
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from scripts.development.accuracy_baseline_metrics import (
    aggregate_cases,
    estimate_usd,
    score_case,
    sum_usage,
    summarize_grounding_items,
)
from scripts.development.accuracy_baseline_privacy import assert_no_pii
from scripts.development.accuracy_baseline_scaffold import (
    FIXTURE_DIR,
    SCAN_FIXTURE,
    TEXT_FIXTURE,
)

from src.domain.services.meal_suggestion.ingredient_name_normalizer import (
    normalize_food_name,
)
from src.domain.services.nutrition_resolver import normalize_food_lookup_name

# Synthetic ids stay inside the process. Reports must not contain them.
_EVAL_MEAL_ID = "00000000-0000-4000-8000-000000000001"
_EVAL_USER_ID = "00000000-0000-4000-8000-000000000002"
_LOOKUP_HITS: ContextVar[dict[str, Any] | None] = ContextVar(
    "accuracy_baseline_lookup_hits",
    default=None,
)
_USAGE: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "accuracy_baseline_usage",
    default=None,
)
_FATSECRET: ContextVar[dict[str, int] | None] = ContextVar(
    "accuracy_baseline_fatsecret",
    default=None,
)
_DEFAULT_REPORT = (
    FIXTURE_DIR.parents[2] / "artifacts" / "accuracy-baseline" / "live-report.json"
)
_COST_NOTE = (
    "Arm totals each include the shared vision and parse-text model call, "
    "so adding the two arm USD figures double-counts that shared call. "
    "incremental_on is the grounding-only FatSecret traffic. "
    "Parse-text ignores AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED."
)


@dataclass
class ItemKey:
    lookup_key: str
    catalog_key: str
    keys_differ: bool
    empty: bool


@dataclass
class ScanCapture:
    case_id: str
    duration_ms: float
    predicted: dict[str, float] | None
    error_type: str | None
    usage: list[dict[str, Any]]
    nutrition: Any = None
    item_keys: tuple[ItemKey, ...] = ()
    exceeded_product_deadline: bool = False


@dataclass
class GroundCapture:
    duration_ms: float
    predicted: dict[str, float] | None
    error_type: str | None
    outcome: str | None
    items: list[dict[str, Any]] = field(default_factory=list)


def default_live_output() -> str:
    return str(_DEFAULT_REPORT)


def require_live_provider_keys() -> None:
    """Fail before any provider call when the meal-scan key is missing."""
    if _openai_key():
        return
    raise RuntimeError(
        "live accuracy baseline requires OPENAI_API_KEY. No provider call was made."
    )


def load_labeled_cases(surface: str) -> list[dict[str, Any]]:
    """Return fixture rows after the QA manifest has already been validated."""
    import json

    path = SCAN_FIXTURE if surface == "meal_scan" else TEXT_FIXTURE
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError(f"{surface} fixture has no cases")
    return [case for case in cases if isinstance(case, dict)]


def key_relation(name: str) -> ItemKey:
    """Compare the cascade lookup key with the catalog's stored key."""
    lookup_key = normalize_food_lookup_name(name)
    catalog_key = normalize_food_name(name)
    return ItemKey(
        lookup_key=lookup_key,
        catalog_key=catalog_key,
        keys_differ=lookup_key != catalog_key,
        empty=not lookup_key,
    )


def prediction_from_nutrition(nutrition: Any) -> dict[str, float]:
    """Use the meal calorie property the product shows after a scan."""
    macros = nutrition.macros
    return {
        "kcal": float(nutrition.calories),
        "protein_g": float(macros.protein),
        "carbs_g": float(macros.carbs),
        "fat_g": float(macros.fat),
    }


async def run_controlled_live(
    scan_cases: list[dict[str, Any]],
    text_cases: list[dict[str, Any]],
    *,
    scan_invoke,
    text_invoke,
    ground_invoke,
    arms: str,
    concurrency: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Score injected callers. Product wiring lives in ``run_product_live``."""
    if arms not in {"off", "on", "both"}:
        raise ValueError("arms must be off, on, or both")
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    scan_captures = await _gather(scan_cases, scan_invoke, concurrency)
    off_scored = [
        score_case(
            case,
            predicted=capture.predicted,
            error_type=capture.error_type,
            duration_ms=capture.duration_ms,
        )
        for case, capture in zip(scan_cases, scan_captures, strict=True)
    ]
    on_scored: list[dict[str, Any]] | None = None
    grounding_rows: list[dict[str, Any]] = []
    grounding_outcomes: dict[str, int] = {}
    if arms in {"on", "both"}:
        grounds = await _gather(scan_captures, ground_invoke, concurrency)
        on_scored = []
        for case, capture, ground in zip(
            scan_cases, scan_captures, grounds, strict=True
        ):
            on_scored.append(
                score_case(
                    case,
                    predicted=ground.predicted,
                    error_type=ground.error_type,
                    duration_ms=capture.duration_ms + ground.duration_ms,
                )
            )
            grounding_rows.extend(ground.items)
            if ground.outcome:
                grounding_outcomes[ground.outcome] = (
                    grounding_outcomes.get(ground.outcome, 0) + 1
                )
    text_captures = await _gather(text_cases, text_invoke, concurrency)
    text_scored = [
        score_case(
            case,
            predicted=capture.predicted,
            error_type=capture.error_type,
            duration_ms=capture.duration_ms,
            exceeded_product_deadline=capture.exceeded_product_deadline,
        )
        for case, capture in zip(text_cases, text_captures, strict=True)
    ]
    report = _report(
        metadata=metadata,
        arms=arms,
        scan_off=off_scored,
        scan_on=on_scored,
        text=text_scored,
        grounding_rows=grounding_rows,
        grounding_outcomes=grounding_outcomes,
        scan_captures=scan_captures,
        text_captures=text_captures,
    )
    assert_no_pii(report, label="live accuracy report")
    return report


def render_live_summary(report: dict[str, Any]) -> str:
    """Short human summary. The JSON report keeps the per-case rows."""
    lines = [
        "Accuracy baseline live sample",
        f"git_sha={report.get('git_sha')}",
        f"arms={report.get('arms')}",
        "Grounding flag was not written. OFF skips ground_meal. "
        "ON calls FoodReferenceValidationService.ground_meal on a copy of "
        "the same vision nutrition.",
        _surface_line("scan grounding off", report["scan"]["grounding_off"]),
    ]
    on_arm = report["scan"].get("grounding_on")
    if on_arm is None:
        lines.append("scan grounding on: not run")
    else:
        lines.append(_surface_line("scan grounding on", on_arm))
        grounding = report["scan"]["grounding_diagnostics"]
        lines.append(
            "local ref items="
            f"{grounding['items']} hits={grounding['local_hits']} "
            f"misses={grounding['local_misses']} "
            f"key_mismatch={grounding['key_mismatch_items']} "
            f"mismatch_and_miss={grounding['key_mismatch_and_local_miss']} "
            f"catalog_key_would_hit={grounding['key_mismatch_catalog_key_would_hit']} "
            f"diagnostic={grounding['catalog_key_diagnostic']} "
            f"tiers={grounding['tiers']}"
        )
    lines.append(_surface_line("prompt", report["text"]))
    cost = report["cost"]
    on_cost = cost["arm_on"] or {}
    incremental = cost["incremental_on"] or {}
    lines.append(
        "cost off_usd="
        f"{cost['arm_off']['usd']} on_usd={on_cost.get('usd')} "
        f"incremental_on_fatsecret_calls={incremental.get('fatsecret_calls')}"
    )
    lines.append(cost["note"])
    lines.append(cost["arm_off"]["method"])
    return "\n".join(lines) + "\n"


async def run_product_live(
    *,
    arms: str,
    concurrency: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Call the product scan, grounding, and parse-text providers."""
    require_live_provider_keys()
    scan_cases = load_labeled_cases("meal_scan")
    text_cases = load_labeled_cases("parse_text")
    bundle = _product_bundle()
    metadata = {
        **metadata,
        "catalog_lookup": bundle["catalog_status"],
        "fatsecret": bundle["fatsecret_status"],
        "grounding_flag_env_changed": False,
        "grounding_timeout_seconds": bundle["grounding_timeout_seconds"],
        "parse_text_eval_deadline_seconds": bundle["parse_text_eval_deadline_seconds"],
        "parse_text_product_deadline_seconds": bundle[
            "parse_text_product_deadline_seconds"
        ],
        "parse_text_pure_ai": bundle["pure_ai"],
        "prod_grounding_default": False,
    }

    async def scan_invoke(case: dict[str, Any]) -> ScanCapture:
        return await _invoke_scan(
            case, service=bundle["vision"], parser=bundle["parser"]
        )

    async def ground_invoke(capture: ScanCapture) -> GroundCapture:
        return await _invoke_ground(
            capture,
            service=bundle["grounding_service"],
            catalog_lookup=bundle["catalog_lookup"],
        )

    async def text_invoke(case: dict[str, Any]) -> ScanCapture:
        return await _invoke_text(
            case,
            handler=bundle["text_handler"],
            product_deadline_seconds=bundle["parse_text_product_deadline_seconds"],
        )

    report = await run_controlled_live(
        scan_cases,
        text_cases,
        scan_invoke=scan_invoke,
        text_invoke=text_invoke,
        ground_invoke=ground_invoke,
        arms=arms,
        concurrency=concurrency,
        metadata=metadata,
    )
    report["cost"]["scan_fatsecret_searches"] = bundle["scan_provider_counter"].searches
    report["cost"]["scan_fatsecret_details"] = bundle["scan_provider_counter"].details
    report["cost"]["text_fatsecret_searches"] = bundle["text_provider_counter"].searches
    report["cost"]["text_fatsecret_details"] = bundle["text_provider_counter"].details
    _fill_fatsecret_cost(report)
    assert_no_pii(report, label="live accuracy report")
    return report


class _CountingProvider:
    """Count FatSecret searches and detail reads without hiding the real client."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.searches = 0
        self.details = 0

    async def search_foods(self, *args: Any, **kwargs: Any) -> Any:
        self._count_search()
        return await self.inner.search_foods(*args, **kwargs)

    async def search_food_candidates(self, *args: Any, **kwargs: Any) -> Any:
        self._count_search()
        return await self.inner.search_food_candidates(*args, **kwargs)

    async def get_food_details(self, *args: Any, **kwargs: Any) -> Any:
        self.details += 1
        holder = _FATSECRET.get()
        if holder is not None:
            holder["details"] += 1
        return await self.inner.get_food_details(*args, **kwargs)

    def _count_search(self) -> None:
        self.searches += 1
        holder = _FATSECRET.get()
        if holder is not None:
            holder["searches"] += 1

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class _LookupProbe:
    """Record the catalog hit map for the task that is grounding one meal."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    async def __call__(self, names: list[str]) -> dict[str, Any]:
        self.calls += 1
        if self.inner is None:
            hits: dict[str, Any] = {}
        else:
            fetched = await self.inner(list(names))
            hits = fetched if isinstance(fetched, dict) else {}
        holder = _LOOKUP_HITS.get()
        if holder is not None:
            holder["hits"] = hits
            holder["requested"] = list(names)
        return hits


def _openai_key() -> str:
    env_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_key:
        return env_key
    from src.infra.config.settings import get_settings

    return str(get_settings().OPENAI_API_KEY or "").strip()


def _database_configured() -> bool:
    if any(
        os.environ.get(name, "").strip()
        for name in ("DATABASE_URL", "APP_DATABASE_URL", "DATABASE_URL_DIRECT")
    ):
        return True
    from src.infra.config.settings import get_settings

    settings = get_settings()
    return bool(
        settings.DATABASE_URL
        or settings.APP_DATABASE_URL
        or settings.DATABASE_URL_DIRECT
    )


def _product_bundle() -> dict[str, Any]:
    from src.api.base_dependencies import (
        get_fat_secret_service_instance,
        get_parse_text_settings,
    )
    from src.app.handlers.command_handlers.parse_meal_text_handler import (
        ParseMealTextHandler,
    )
    from src.app.services.food_reference_validation_service import (
        FoodReferenceValidationService,
    )
    from src.domain.parsers.vision_response_parser import VisionResponseParser
    from src.infra.adapters.meal_generation_service import MealGenerationService
    from src.infra.adapters.vision_ai_service import VisionAIService
    from src.infra.config.settings import get_settings
    from src.infra.services.ai.ai_model_manager import AIModelManager

    settings = get_settings()
    parse_settings = get_parse_text_settings()
    _install_usage_tap(AIModelManager.get_instance())
    fatsecret = get_fat_secret_service_instance()
    scan_counter = (
        _CountingProvider(fatsecret)
        if fatsecret is not None
        else _CountingProvider(_NullProvider())
    )
    text_counter = (
        _CountingProvider(fatsecret)
        if fatsecret is not None
        else _CountingProvider(_NullProvider())
    )
    catalog_lookup = _catalog_batch_lookup() if _database_configured() else None
    probe = _LookupProbe(catalog_lookup)
    timeout = float(settings.AI_MEAL_ANALYZE_EXTERNAL_PROVIDER_TIMEOUT_SECONDS)
    grounding = FoodReferenceValidationService(
        food_reference_batch_lookup=probe,
        nutrition_reference_provider=None if fatsecret is None else scan_counter,
        timeout_seconds=timeout,
    )
    product_deadline = float(settings.PARSE_TEXT_AI_TIMEOUT_SECONDS)
    eval_deadline = min(120.0, max(product_deadline, 45.0))
    handler = ParseMealTextHandler(
        meal_generation_service=MealGenerationService(),
        fat_secret_service=None if fatsecret is None else text_counter,
        food_reference_batch_lookup=catalog_lookup,
        structured_reference_enabled=bool(
            parse_settings.get("structured_reference_enabled", True)
        ),
        pure_ai_mode=bool(parse_settings.get("pure_ai_mode", False)),
        cache_service=None,
        uow_factory=None,
        ai_timeout_seconds=eval_deadline,
        cache_namespace=str(settings.OPENAI_PARSE_TEXT_MODEL),
        reference_timeout_seconds=float(settings.PARSE_TEXT_FATSECRET_TIMEOUT_SECONDS),
    )
    if fatsecret is None:
        scan_counter.searches = 0
        scan_counter.details = 0
    return {
        "catalog_lookup": catalog_lookup,
        "catalog_status": "configured"
        if catalog_lookup is not None
        else "skipped_no_database",
        "fatsecret_status": "configured"
        if fatsecret is not None
        else "skipped_no_credentials",
        "grounding_service": grounding,
        "grounding_timeout_seconds": timeout,
        "parser": VisionResponseParser(),
        "parse_text_eval_deadline_seconds": eval_deadline,
        "parse_text_product_deadline_seconds": product_deadline,
        "pure_ai": bool(parse_settings.get("pure_ai_mode", False)),
        "scan_provider_counter": scan_counter,
        "text_handler": handler,
        "text_provider_counter": text_counter,
        "vision": VisionAIService(),
    }


class _NullProvider:
    async def search_food_candidates(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def get_food_details(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _catalog_batch_lookup():
    from src.infra.database.uow_async import AsyncUnitOfWork

    async def lookup(names: list[str]) -> dict[str, Any]:
        async with AsyncUnitOfWork() as uow:
            return await uow.food_references.find_batch_by_normalized_names(names)

    return lookup


def _install_usage_tap(manager: Any) -> None:
    provider = getattr(manager, "_providers", {}).get("openai")
    if provider is None or getattr(provider, "_accuracy_baseline_usage_tapped", False):
        return
    original = provider._record_prompt_cache_usage

    def wrapped(raw_message: Any, *, model: str, purpose_hint: str | None) -> None:
        original(raw_message, model=model, purpose_hint=purpose_hint)
        bucket = _USAGE.get()
        if bucket is None:
            return
        usage = _usage_from_message(raw_message)
        cached = provider._langchain.cached_tokens(raw_message)
        bucket.append(
            {
                "cached_tokens": int(cached or 0),
                "input_tokens": int(usage.get("input_tokens") or 0),
                "model": model,
                "output_tokens": int(usage.get("output_tokens") or 0),
                "purpose": purpose_hint or "",
            }
        )

    provider._record_prompt_cache_usage = wrapped
    provider._accuracy_baseline_usage_tapped = True


def _usage_from_message(message: Any) -> dict[str, int]:
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict):
        return {}
    parsed: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and value >= 0:
            parsed[key] = value
    return parsed


async def _invoke_scan(
    case: dict[str, Any], *, service: Any, parser: Any
) -> ScanCapture:
    started = time.perf_counter()
    bucket: list[dict[str, Any]] = []
    token = _USAGE.set(bucket)
    predicted = None
    nutrition = None
    item_keys: tuple[ItemKey, ...] = ()
    error_type = None
    try:
        image_path = FIXTURE_DIR / str(case["image_path"])
        result = await service.analyze(
            image_path.read_bytes(),
            language=str(case.get("language") or "en"),
        )
        if not parser.parse_is_food(result):
            error_type = "NotFood"
        else:
            nutrition = parser.parse_to_nutrition(result)
            predicted = prediction_from_nutrition(nutrition)
            item_keys = tuple(
                key_relation(str(item.name or ""))
                for item in (nutrition.food_items or [])
            )
    except Exception as exc:
        error_type = type(exc).__name__
        predicted = None
        nutrition = None
    finally:
        _USAGE.reset(token)
    return ScanCapture(
        case_id=str(case["case_id"]),
        duration_ms=(time.perf_counter() - started) * 1000,
        predicted=predicted,
        error_type=error_type,
        usage=list(bucket),
        nutrition=nutrition,
        item_keys=item_keys,
    )


async def _invoke_ground(
    capture: ScanCapture,
    *,
    service: Any,
    catalog_lookup,
) -> GroundCapture:
    if capture.nutrition is None:
        return GroundCapture(
            duration_ms=0.0,
            predicted=None,
            error_type=capture.error_type or "NoVision",
            outcome=None,
            items=[],
        )
    from src.domain.model.meal.meal import Meal, MealStatus
    from src.domain.utils.timezone_utils import utc_now

    nutrition = copy.deepcopy(capture.nutrition)
    meal = Meal(
        meal_id=_EVAL_MEAL_ID,
        user_id=_EVAL_USER_ID,
        status=MealStatus.PROCESSING,
        created_at=utc_now(),
        image=None,
        nutrition=nutrition,
        source="scanner",
    )
    holder: dict[str, Any] = {}
    token = _LOOKUP_HITS.set(holder)
    started = time.perf_counter()
    error_type = None
    outcome = None
    try:
        report = await service.ground_meal(meal)
        outcome = str(report.outcome)
    except Exception as exc:
        error_type = type(exc).__name__
    finally:
        _LOOKUP_HITS.reset(token)
    duration_ms = (time.perf_counter() - started) * 1000
    hits = holder.get("hits") if isinstance(holder.get("hits"), dict) else {}
    items = _grounding_rows(capture.item_keys, nutrition, hits)
    await _mark_catalog_key_hits(items, catalog_lookup)
    predicted = None
    if error_type is None and meal.nutrition is not None:
        predicted = prediction_from_nutrition(meal.nutrition)
    return GroundCapture(
        duration_ms=duration_ms,
        predicted=predicted,
        error_type=error_type,
        outcome=outcome,
        items=items,
    )


def _grounding_rows(
    item_keys: tuple[ItemKey, ...],
    nutrition: Any,
    hits: dict[str, Any],
) -> list[dict[str, Any]]:
    foods = list(getattr(nutrition, "food_items", None) or [])
    rows: list[dict[str, Any]] = []
    for key, food in zip(item_keys, foods, strict=False):
        tier = str(getattr(food, "source_kind", None) or "ai_estimate")
        rows.append(
            {
                "catalog_key_would_hit": None,
                "empty": key.empty,
                "keys_differ": key.keys_differ,
                "product_local_hit": bool(key.lookup_key) and key.lookup_key in hits,
                "tier": tier,
                "_catalog_key": key.catalog_key,
                "_needs_diagnostic": key.keys_differ
                and not (bool(key.lookup_key) and key.lookup_key in hits),
            }
        )
    return rows


async def _mark_catalog_key_hits(rows: list[dict[str, Any]], catalog_lookup) -> None:
    needed = sorted(
        {
            str(row["_catalog_key"])
            for row in rows
            if row["_needs_diagnostic"] and row["_catalog_key"]
        }
    )
    diagnostic_hits: dict[str, Any] | None
    if catalog_lookup is None:
        diagnostic_hits = None
    elif not needed:
        diagnostic_hits = {}
    else:
        try:
            fetched = await catalog_lookup(needed)
            diagnostic_hits = fetched if isinstance(fetched, dict) else {}
        except Exception:
            diagnostic_hits = None
    for row in rows:
        if not row["_needs_diagnostic"]:
            row["catalog_key_would_hit"] = False
        elif diagnostic_hits is None:
            row["catalog_key_would_hit"] = None
        else:
            row["catalog_key_would_hit"] = row["_catalog_key"] in diagnostic_hits
        row.pop("_catalog_key", None)
        row.pop("_needs_diagnostic", None)


async def _invoke_text(
    case: dict[str, Any],
    *,
    handler: Any,
    product_deadline_seconds: float,
) -> ScanCapture:
    from src.api.routes.v1.meals_route_helpers import parse_meal_text_response
    from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand

    started = time.perf_counter()
    bucket: list[dict[str, Any]] = []
    fatsecret_counts = {"details": 0, "searches": 0}
    token = _USAGE.set(bucket)
    fatsecret_token = _FATSECRET.set(fatsecret_counts)
    predicted = None
    error_type = None
    try:
        dto = await handler.handle(
            ParseMealTextCommand(
                text=str(case.get("text") or ""),
                language=str(case.get("language") or "en"),
                user_id=None,
            )
        )
        response = parse_meal_text_response(dto)
        predicted = {
            "kcal": float(response.total_calories),
            "protein_g": float(response.total_protein),
            "carbs_g": float(response.total_carbs),
            "fat_g": float(response.total_fat),
        }
    except Exception as exc:
        error_type = type(exc).__name__
    finally:
        _USAGE.reset(token)
        _FATSECRET.reset(fatsecret_token)
    duration_ms = (time.perf_counter() - started) * 1000
    usage = list(bucket)
    usage.append(
        {
            "cached_tokens": 0,
            "fatsecret_details": fatsecret_counts["details"],
            "fatsecret_searches": fatsecret_counts["searches"],
            "input_tokens": 0,
            "model": "",
            "output_tokens": 0,
            "purpose": "parse_text_reference_count",
        }
    )
    return ScanCapture(
        case_id=str(case["case_id"]),
        duration_ms=duration_ms,
        predicted=predicted,
        error_type=error_type,
        usage=usage,
        exceeded_product_deadline=duration_ms > product_deadline_seconds * 1000,
    )


async def _gather(items: list[Any], invoke, concurrency: int) -> list[Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def _one(index: int, item: Any) -> tuple[int, Any]:
        async with semaphore:
            return index, await invoke(item)

    pairs = await asyncio.gather(
        *(_one(index, item) for index, item in enumerate(items))
    )
    ordered = [None] * len(items)
    for index, value in pairs:
        ordered[index] = value
    return ordered


def _report(
    *,
    metadata: dict[str, Any],
    arms: str,
    scan_off: list[dict[str, Any]],
    scan_on: list[dict[str, Any]] | None,
    text: list[dict[str, Any]],
    grounding_rows: list[dict[str, Any]],
    grounding_outcomes: dict[str, int],
    scan_captures: list[ScanCapture],
    text_captures: list[ScanCapture],
) -> dict[str, Any]:
    vision_usage = sum_usage(
        [record for capture in scan_captures for record in capture.usage]
    )
    text_usage = sum_usage(
        [
            record
            for capture in text_captures
            for record in capture.usage
            if record.get("purpose") != "parse_text_reference_count"
        ]
    )
    text_reference_calls = sum(
        int(record.get("fatsecret_searches") or 0)
        + int(record.get("fatsecret_details") or 0)
        for capture in text_captures
        for record in capture.usage
    )
    shared = {
        "cached_tokens": vision_usage["cached_tokens"] + text_usage["cached_tokens"],
        "calls": vision_usage["calls"] + text_usage["calls"],
        "input_tokens": vision_usage["input_tokens"] + text_usage["input_tokens"],
        "output_tokens": vision_usage["output_tokens"] + text_usage["output_tokens"],
    }
    scan_on_summary = None if scan_on is None else aggregate_cases(scan_on)
    grounding = None if scan_on is None else summarize_grounding_items(grounding_rows)
    if grounding is not None:
        grounding["meal_outcomes"] = grounding_outcomes
    off_cost = estimate_usd(shared, fatsecret_calls=text_reference_calls)
    on_cost = (
        None
        if scan_on is None
        else estimate_usd(shared, fatsecret_calls=text_reference_calls)
    )
    return {
        "accuracy_claim": _headline(
            aggregate_cases(scan_off), scan_on_summary, aggregate_cases(text)
        ),
        "arms": arms,
        "candidate_quality_measured": True,
        "cost": {
            "arm_off": {**off_cost, "includes_shared_model_call": True},
            "arm_on": None
            if on_cost is None
            else {**on_cost, "includes_shared_model_call": True},
            "incremental_on": None
            if scan_on is None
            else {
                "fatsecret_calls": 0,
                "method": "Filled after the grounding arm returns provider counts.",
                "token_usd": 0.0,
                "usd": None,
            },
            "note": _COST_NOTE,
            "shared_model": {
                "parse_text": text_usage,
                "vision": vision_usage,
            },
            "text_reference_calls": text_reference_calls,
        },
        "git_sha": metadata.get("git_sha"),
        "grounding_arm": {
            "flag": "AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED",
            "follow_up": (
                "The harness does not set this flag. ON calls ground_meal, "
                "which is the production branch the flag selects."
            ),
            "implemented": True,
            "process_env_changed": False,
            "prod_default": False,
            "requested": arms,
        },
        "metadata": metadata,
        "provider_calls": shared["calls"] + text_reference_calls,
        "report_kind": "accuracy_baseline_live",
        "scan": {
            "grounding_diagnostics": grounding,
            "grounding_off": {
                **aggregate_cases(scan_off),
                "cases": _public_cases(scan_off),
            },
            "grounding_on": None
            if scan_on_summary is None
            else {**scan_on_summary, "cases": _public_cases(scan_on or [])},
        },
        "schema_version": 1,
        "text": {
            **aggregate_cases(text),
            "cases": _public_cases(text),
            "grounding_flag_applies": False,
        },
    }


def _fill_fatsecret_cost(report: dict[str, Any]) -> None:
    """Add scan grounding calls onto the ON arm after the counters settle."""
    scan_calls = int(report["cost"].get("scan_fatsecret_searches") or 0) + int(
        report["cost"].get("scan_fatsecret_details") or 0
    )
    text_calls = int(report["cost"].get("text_reference_calls") or 0)
    shared_usage = {
        "input_tokens": report["cost"]["shared_model"]["vision"]["input_tokens"]
        + report["cost"]["shared_model"]["parse_text"]["input_tokens"],
        "output_tokens": report["cost"]["shared_model"]["vision"]["output_tokens"]
        + report["cost"]["shared_model"]["parse_text"]["output_tokens"],
    }
    if report["scan"]["grounding_on"] is not None:
        report["cost"]["arm_on"] = {
            **estimate_usd(shared_usage, fatsecret_calls=text_calls + scan_calls),
            "includes_shared_model_call": True,
        }
        report["cost"]["incremental_on"] = {
            **estimate_usd(
                {"input_tokens": 0, "output_tokens": 0},
                fatsecret_calls=scan_calls,
            ),
            "includes_shared_model_call": False,
        }
    report["provider_calls"] = (
        report["cost"]["shared_model"]["vision"]["calls"]
        + report["cost"]["shared_model"]["parse_text"]["calls"]
        + text_calls
        + scan_calls
    )


def _headline(
    scan_off: dict[str, Any],
    scan_on: dict[str, Any] | None,
    text: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "single_live_sample",
        "release_gate": False,
        "scan_grounding_off": _headline_nutrients(scan_off),
        "scan_grounding_on": None if scan_on is None else _headline_nutrients(scan_on),
        "text": _headline_nutrients(text),
    }


def _headline_nutrients(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        nutrient: {
            "mae": values["mae"],
            "mape": values["mape"],
            "scored_cases": values["scored_cases"],
            "within_10_rate": values["within_10_rate"],
            "within_20_rate": values["within_20_rate"],
        }
        for nutrient, values in summary["nutrients"].items()
    }


def _public_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop nothing that is already safe. Cases carry ids, errors, and numbers."""
    public: list[dict[str, Any]] = []
    for case in cases:
        nutrients = {}
        for name, item in case["nutrients"].items():
            nutrients[name] = {
                key: value
                for key, value in item.items()
                if key != "reason" or not item["scored"]
            }
        public.append(
            {
                "case_id": case["case_id"],
                "duration_ms": case["duration_ms"],
                "error_type": case["error_type"],
                "exceeded_product_deadline": case["exceeded_product_deadline"],
                "label_status": case["label_status"],
                "nutrients": nutrients,
                "outcome": case["outcome"],
                "split": case["split"],
            }
        )
    return public


def _surface_line(label: str, summary: dict[str, Any]) -> str:
    nutrients = summary["nutrients"]
    parts = [label]
    for name in ("kcal", "protein_g", "carbs_g", "fat_g"):
        item = nutrients[name]
        parts.append(
            f"{name} mae={_num(item['mae'])} "
            f"within20={_pct(item['within_20_rate'])} "
            f"n={item['scored_cases']}"
        )
    latency = summary["latency_ms"]
    parts.append(
        f"latency_p50_ms={_num(latency['p50_ms'])} "
        f"p95_ms={_num(latency['p95_ms'])} "
        f"provider_errors={summary['provider_error_cases']} "
        f"unscored={summary['unscored_label_cases']} "
        f"partial={summary['partial_cases']}"
    )
    return " | ".join(parts)


def _num(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.3f}"


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.1f}%"
