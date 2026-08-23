#!/usr/bin/env python3
"""Run offline parse-text quality gates or an explicitly guarded staging eval."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.api.routes.v1.meals_route_helpers import parsed_food_item_to_response
from src.api.schemas.response.meal_responses import ParseMealTextResponse
from src.app.commands.meal.parse_meal_text_command import ParseMealTextCommand
from src.app.handlers.command_handlers.parse_meal_text_handler import (
    ParseMealTextHandler,
)
from src.domain.services.meal_text_nutrition_eval_loop import (
    ParseTextEvalCase,
    ParseTextEvalObservation,
    ParseTextEvalSummary,
    ParseTextNutritionEvalLoop,
)
from src.domain.services.nutrition_resolver import normalize_food_lookup_name

CORPUS_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "parse_text_nutrition_golden_cases.json"
)
CASE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
LIVE_MAX_CASES = 25
LIVE_MAX_AI_GENERATIONS = 50
LIVE_MAX_SEARCHES = 25
LIVE_MAX_DETAILS = 25
LIVE_TIMEOUT_SECONDS = 300.0
MAX_HANDLER_ITEMS = 8


@dataclass(frozen=True)
class ParseTextDropCase:
    case_id: str
    text: str
    language: str
    expected_unmatched_terms: tuple[str, ...]
    ai_payload: dict[str, Any]
    provider_candidates: list[dict[str, Any]]
    provider_details: dict[str, dict[str, Any]]
    local_reference: dict[str, Any] | None = None


def load_corpus(
    path: Path = CORPUS_PATH,
) -> tuple[list[ParseTextEvalCase], list[ParseTextDropCase]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("evaluation corpus must be a non-empty JSON array")
    cases: list[ParseTextEvalCase] = []
    drop_cases: list[ParseTextDropCase] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("evaluation corpus entries must be objects")
        case_id = str(entry.get("case_id") or "")
        if not CASE_ID_PATTERN.fullmatch(case_id) or case_id in seen:
            raise ValueError(
                "evaluation case IDs must be unique, lowercase, and synthetic"
            )
        if not str(entry.get("text") or "").strip():
            raise ValueError(f"evaluation case {case_id} has no text")
        seen.add(case_id)
        if entry.get("expected_drop"):
            drop_cases.append(
                ParseTextDropCase(
                    case_id=case_id,
                    text=str(entry["text"]),
                    language=str(entry.get("language") or "en"),
                    expected_unmatched_terms=tuple(
                        str(term)
                        for term in entry.get("expected_unmatched_terms") or []
                    ),
                    ai_payload=entry["ai_payload"],
                    provider_candidates=entry.get("provider_candidates", []),
                    provider_details=entry.get("provider_details", {}),
                    local_reference=entry.get("local_reference"),
                )
            )
            continue
        cases.append(
            ParseTextEvalCase(
                case_id=case_id,
                text=str(entry["text"]),
                language=str(entry.get("language") or "en"),
                expected_lookup_name=normalize_food_lookup_name(
                    str(entry["expected_lookup_name"])
                ),
                expected_quantity_g=float(entry["expected_quantity_g"]),
                expected_source=str(entry["expected_source"]),
                expected_calorie_range=tuple(entry["expected_calorie_range"]),
                expected_candidate_id=entry.get("expected_candidate_id"),
                ai_payload=entry["ai_payload"],
                provider_candidates=entry.get("provider_candidates", []),
                provider_details=entry.get("provider_details", {}),
                local_reference=entry.get("local_reference"),
            )
        )
    if not cases and not drop_cases:
        raise ValueError("evaluation corpus must contain at least one case")
    return cases, drop_cases


class _OfflineAI:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.calls = 0

    async def generate_meal_plan_async(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return self.payload


class _OfflineProvider:
    def __init__(self, case: ParseTextEvalCase):
        self.case = case
        self.searches = 0
        self.details = 0
        self.detail_ids: list[str] = []

    async def search_food_candidates(
        self, _query: str, **_kwargs: Any
    ) -> list[dict[str, Any]]:
        self.searches += 1
        return self.case.provider_candidates

    async def get_food_details(
        self, food_id: str, **_kwargs: Any
    ) -> dict[str, Any] | None:
        self.details += 1
        self.detail_ids.append(food_id)
        return self.case.provider_details.get(food_id)


class _CountingLiveAI:
    def __init__(self, delegate: Any):
        self.delegate = delegate
        self.calls = 0
        self.last_payload: dict[str, Any] = {}

    async def generate_meal_plan_async(self, **kwargs: Any) -> Any:
        self.calls += 1
        self.last_payload = await self.delegate.generate_meal_plan_async(**kwargs)
        return self.last_payload


class _CountingLiveProvider:
    def __init__(self, delegate: Any):
        self.delegate = delegate
        self.searches = 0
        self.details = 0
        self.detail_ids: list[str] = []

    async def search_food_candidates(self, *args: Any, **kwargs: Any) -> Any:
        self.searches += 1
        return await self.delegate.search_food_candidates(*args, **kwargs)

    async def get_food_details(self, food_id: str, *args: Any, **kwargs: Any) -> Any:
        self.details += 1
        self.detail_ids.append(food_id)
        return await self.delegate.get_food_details(food_id, *args, **kwargs)


async def _run_drop_case(case: ParseTextDropCase) -> None:
    """Unmatched foods are dropped and must not contribute kcal."""
    eval_case = ParseTextEvalCase(
        case_id=case.case_id,
        text=case.text,
        language=case.language,
        expected_lookup_name="",
        expected_quantity_g=0.0,
        expected_source="unmatched",
        expected_calorie_range=(0.0, 0.0),
        ai_payload=case.ai_payload,
        provider_candidates=case.provider_candidates,
        provider_details=case.provider_details,
        local_reference=case.local_reference,
    )
    observation = await _run_case(eval_case, live=False)
    response = observation.response
    if response.items:
        raise ValueError(
            f"{case.case_id}: dropped foods must not appear in parse-text items"
        )
    if float(response.total_calories or 0.0) != 0.0:
        raise ValueError(
            f"{case.case_id}: unmatched foods must not contribute kcal"
        )
    if list(response.unmatched_terms) != list(case.expected_unmatched_terms):
        raise ValueError(
            f"{case.case_id}: expected unmatched_terms "
            f"{list(case.expected_unmatched_terms)!r}, got "
            f"{list(response.unmatched_terms)!r}"
        )


async def _run_case(
    case: ParseTextEvalCase, *, live: bool = False
) -> ParseTextEvalObservation:
    started = time.perf_counter()
    if live:
        from src.api.base_dependencies import get_fat_secret_service_instance
        from src.infra.adapters.meal_generation_service import MealGenerationService

        ai = _CountingLiveAI(MealGenerationService())
        provider = _CountingLiveProvider(get_fat_secret_service_instance())
    else:
        ai = _OfflineAI(case.ai_payload)
        provider = _OfflineProvider(case)

    async def local_lookup(names: list[str]) -> dict[str, dict[str, Any]]:
        if case.local_reference is None:
            return {}
        key = normalize_food_lookup_name(case.local_reference.get("name", ""))
        return {name: case.local_reference for name in names if name == key}

    handler = ParseMealTextHandler(
        meal_generation_service=ai,
        fat_secret_service=provider,
        food_reference_batch_lookup=local_lookup,
        structured_reference_enabled=True,
    )
    response = await handler.handle(
        ParseMealTextCommand(text=case.text, language=case.language)
    )
    public = ParseMealTextResponse(
        items=[parsed_food_item_to_response(item) for item in response.items],
        total_calories=sum(
            parsed_food_item_to_response(item).calories for item in response.items
        ),
        total_protein=response.total_protein,
        total_carbs=response.total_carbs,
        total_fat=response.total_fat,
        emoji=response.emoji,
        unmatched_terms=response.unmatched_terms,
    )
    ai_item = getattr(ai, "last_payload", case.ai_payload).get("items", [{}])[0]
    extracted_lookup = ai_item.get("lookup_name")
    extracted_quantity = ai_item.get("quantity_g")
    return ParseTextEvalObservation(
        response=public,
        extracted_lookup_name=normalize_food_lookup_name(extracted_lookup)
        if extracted_lookup
        else None,
        extracted_quantity_g=float(extracted_quantity)
        if extracted_quantity is not None
        else None,
        selected_candidate_id=(
            provider.detail_ids[-1] if provider.detail_ids else None
        ),
        provider_searches=getattr(provider, "searches", 0),
        provider_details=getattr(provider, "details", 0),
        duration_ms=(time.perf_counter() - started) * 1000,
    )


async def run_offline(
    cases: list[ParseTextEvalCase],
    drop_cases: list[ParseTextDropCase],
) -> ParseTextEvalSummary:
    for drop_case in drop_cases:
        await _run_drop_case(drop_case)
    loop = ParseTextNutritionEvalLoop()
    summary = await loop.evaluate(cases, lambda case: _run_case(case))
    loop.enforce_gates(summary)
    return summary


async def run_live(cases: list[ParseTextEvalCase]) -> ParseTextEvalSummary:
    """Run a sequential, capped staging sample against configured providers."""
    started = time.monotonic()
    deadline = started + LIVE_TIMEOUT_SECONDS
    remaining_ai = LIVE_MAX_AI_GENERATIONS
    remaining_searches = LIVE_MAX_SEARCHES
    remaining_details = LIVE_MAX_DETAILS
    observations: list[tuple[ParseTextEvalCase, ParseTextEvalObservation]] = []
    for original in cases[:LIVE_MAX_CASES]:
        case_item_count = _case_item_count(original)
        if (
            time.monotonic() >= deadline
            or remaining_ai < 2
            or remaining_searches < case_item_count
            or remaining_details < case_item_count
        ):
            break
        case = original.__class__(
            **{**original.__dict__, "expected_candidate_id": None}
        )
        remaining_seconds = deadline - time.monotonic()
        try:
            observation = await asyncio.wait_for(
                _run_case(case, live=True), timeout=remaining_seconds
            )
        except TimeoutError:
            break
        observations.append((case, observation))
        remaining_ai -= 2
        remaining_searches -= observation.provider_searches
        remaining_details -= observation.provider_details
    if not observations:
        raise RuntimeError("live evaluation budget admitted no cases")
    loop = ParseTextNutritionEvalLoop()
    return await loop.evaluate(
        [case for case, _ in observations],
        _observation_replay(observations),
    )


def _case_item_count(case: ParseTextEvalCase) -> int:
    """Reserve worst-case provider capacity before admitting a live case."""
    raw_items = case.ai_payload.get("items")
    if not isinstance(raw_items, list):
        return 1
    return max(1, min(len(raw_items), MAX_HANDLER_ITEMS))


def _observation_replay(
    observations: list[tuple[ParseTextEvalCase, ParseTextEvalObservation]],
):
    iterator = iter(observations)

    async def replay(_case: ParseTextEvalCase) -> ParseTextEvalObservation:
        return next(iterator)[1]

    return replay


def assert_live_staging_allowed(confirm_live_staging: bool) -> None:
    if os.getenv("ENVIRONMENT") != "staging":
        raise RuntimeError("live eval requires authoritative ENVIRONMENT=staging")
    if os.getenv("PARSE_TEXT_LIVE_EVAL_ENABLED", "").lower() != "true":
        raise RuntimeError("live eval requires PARSE_TEXT_LIVE_EVAL_ENABLED=true")
    if not confirm_live_staging:
        raise RuntimeError("live eval requires --confirm-live-staging")


def _open_report(path: str | None) -> tuple[Path, Any]:
    if path is None:
        fd, raw_path = tempfile.mkstemp(prefix="parse-text-eval-", suffix=".json")
        return Path(raw_path), os.fdopen(fd, "w", encoding="utf-8")
    target = Path(path).expanduser().resolve()
    if target.exists():
        raise RuntimeError("refusing to overwrite an existing evaluation report")
    if target.is_relative_to(REPO_ROOT):
        checked = subprocess.run(
            ["git", "check-ignore", "--no-index", str(target)],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if checked.returncode != 0:
            raise RuntimeError("repository report paths must already be git-ignored")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    return target, os.fdopen(fd, "w", encoding="utf-8")


def write_report(summary: ParseTextEvalSummary, path: str | None) -> Path:
    target, handle = _open_report(path)
    with handle:
        json.dump(summary.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(target, 0o600)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--output")
    parser.add_argument("--max-cases", type=int, default=25)
    parser.add_argument("--confirm-live-staging", action="store_true")
    args = parser.parse_args(argv)
    try:
        reference_cases, drop_cases = load_corpus()
        max_cases = max(1, min(args.max_cases, 25))
        reference_cases = reference_cases[:max_cases]
        drop_cases = drop_cases[:max_cases]
        if args.mode == "live":
            assert_live_staging_allowed(args.confirm_live_staging)
            summary = asyncio.run(run_live(reference_cases))
            ParseTextNutritionEvalLoop.enforce_gates(summary)
            path = write_report(summary, args.output)
            print(
                f"mode=live cases={summary.case_count} p50_ms={summary.latency_p50_ms:.3f} p95_ms={summary.latency_p95_ms:.3f} report={path}"
            )
            return 0
        summary = asyncio.run(run_offline(reference_cases, drop_cases))
        path = write_report(summary, args.output)
        total_cases = summary.case_count + len(drop_cases)
        drop_rate = len(drop_cases) / total_cases if total_cases else 0.0
        print(
            f"mode=offline cases={summary.case_count} drop_cases={len(drop_cases)} "
            f"drop_rate={drop_rate:.3f} p50_ms={summary.latency_p50_ms:.3f} "
            f"p95_ms={summary.latency_p95_ms:.3f} report={path}"
        )
        return 0
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"parse-text evaluation failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
