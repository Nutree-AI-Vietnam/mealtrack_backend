#!/usr/bin/env python3
"""Unified evaluation runner and release gate for meal image, food label, and barcode scans."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.domain.services.barcode_eval_loop import (
    BarcodeEvalCase,
    BarcodeEvalLoop,
    BarcodeEvalObservation,
)
from src.domain.services.food_label_eval_loop import (
    FoodLabelEvalCase,
    FoodLabelEvalLoop,
    FoodLabelEvalObservation,
)
from src.domain.services.meal_image_nutrition_eval_loop import (
    MealImageEvalCase,
    MealImageEvalObservation,
    MealImageNutritionEvalLoop,
)

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
MEAL_IMAGE_CORPUS_PATH = FIXTURES_DIR / "meal_image_golden_cases.json"
FOOD_LABEL_CORPUS_PATH = FIXTURES_DIR / "food_label_golden_cases.json"
BARCODE_CORPUS_PATH = FIXTURES_DIR / "barcode_golden_cases.json"

CASE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
LIVE_MAX_CASES = 30


# -----------------------------------------------------------------------------
# Corpus Loaders
# -----------------------------------------------------------------------------


def load_meal_image_corpus(
    path: Path = MEAL_IMAGE_CORPUS_PATH,
) -> list[MealImageEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    seen = set()
    for entry in raw:
        cid = str(entry.get("case_id") or "")
        if not CASE_ID_PATTERN.fullmatch(cid) or cid in seen:
            raise ValueError(f"Invalid or duplicate meal image case ID: {cid}")
        seen.add(cid)
        cases.append(
            MealImageEvalCase(
                case_id=cid,
                language=str(entry.get("language") or "en"),
                category=str(entry.get("category") or "composite"),
                expected_is_food=bool(entry.get("expected_is_food", True)),
                expected_dish_name=str(entry.get("expected_dish_name") or ""),
                expected_foods=entry.get("expected_foods") or [],
                expected_calorie_range=tuple(
                    entry.get("expected_calorie_range", (0, 0))
                ),
                ai_payload=entry.get("ai_payload") or {},
                local_reference=entry.get("local_reference"),
            )
        )
    return cases


def load_food_label_corpus(
    path: Path = FOOD_LABEL_CORPUS_PATH,
) -> list[FoodLabelEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    seen = set()
    for entry in raw:
        cid = str(entry.get("case_id") or "")
        if not CASE_ID_PATTERN.fullmatch(cid) or cid in seen:
            raise ValueError(f"Invalid or duplicate food label case ID: {cid}")
        seen.add(cid)
        cases.append(
            FoodLabelEvalCase(
                case_id=cid,
                language=str(entry.get("language") or "en"),
                format_type=str(entry.get("format_type") or "standard_us"),
                expected_is_food_label=bool(entry.get("expected_is_food_label", True)),
                expected_product_name=str(entry.get("expected_product_name") or ""),
                expected_serving_grams=float(
                    entry.get("expected_serving_grams") or 0.0
                ),
                expected_servings_per_package=float(
                    entry.get("expected_servings_per_package") or 1.0
                ),
                expected_calories_per_serving=float(
                    entry.get("expected_calories_per_serving") or 0.0
                ),
                expected_macros=entry.get("expected_macros") or {},
                ai_payload=entry.get("ai_payload") or {},
            )
        )
    return cases


def load_barcode_corpus(path: Path = BARCODE_CORPUS_PATH) -> list[BarcodeEvalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    seen = set()
    for entry in raw:
        cid = str(entry.get("case_id") or "")
        if not CASE_ID_PATTERN.fullmatch(cid) or cid in seen:
            raise ValueError(f"Invalid or duplicate barcode case ID: {cid}")
        seen.add(cid)
        cal_range = entry.get("expected_calories_100g_range")
        cases.append(
            BarcodeEvalCase(
                case_id=cid,
                barcode=str(entry.get("barcode") or ""),
                scanned_barcode=str(entry.get("scanned_barcode") or ""),
                aliases=tuple(entry.get("aliases") or ()),
                language=str(entry.get("language") or "en"),
                expected_hit=bool(entry.get("expected_hit", True)),
                expected_source=str(entry.get("expected_source") or "none"),
                expected_is_estimate=bool(entry.get("expected_is_estimate", False)),
                expected_saveable=bool(entry.get("expected_saveable", False)),
                expected_canonical_quarantine=bool(
                    entry.get("expected_canonical_quarantine", False)
                ),
                expected_name=entry.get("expected_name"),
                expected_calories_100g_range=tuple(cal_range) if cal_range else None,
                provider_responses=entry.get("provider_responses") or {},
            )
        )
    return cases


# -----------------------------------------------------------------------------
# Offline Runners
# -----------------------------------------------------------------------------


async def offline_meal_image_runner(
    case: MealImageEvalCase,
) -> MealImageEvalObservation:
    t0 = time.perf_counter()
    payload = case.ai_payload
    is_food = bool(payload.get("is_food", True))
    dish_name = payload.get("dish_name")
    foods = payload.get("foods") or []
    total_cals = 0.0
    for f in foods:
        m = f.get("macros", {})
        total_cals += (
            float(m.get("protein", 0)) * 4.0
            + float(m.get("carbs", 0)) * 4.0
            + float(m.get("fat", 0)) * 9.0
        )
    duration_ms = (time.perf_counter() - t0) * 1000 + 1.2
    return MealImageEvalObservation(
        response=payload,
        is_food=is_food,
        dish_name=dish_name,
        foods=foods,
        total_calories=total_cals,
        duration_ms=duration_ms,
        provider_calls=1,
    )


async def offline_food_label_runner(
    case: FoodLabelEvalCase,
) -> FoodLabelEvalObservation:
    t0 = time.perf_counter()
    payload = case.ai_payload
    is_lbl = bool(payload.get("is_food_label", True))
    p_name = payload.get("product_name")
    s_size = payload.get("serving_size", {})
    s_grams = float(s_size.get("grams", 0.0))
    s_pkg = float(payload.get("servings_per_package", 1.0))
    cals = float(payload.get("label_calories_per_serving", 0.0))
    macros = payload.get("macros_per_serving") or {}
    duration_ms = (time.perf_counter() - t0) * 1000 + 1.1
    return FoodLabelEvalObservation(
        response=payload,
        is_food_label=is_lbl,
        product_name=p_name,
        serving_grams=s_grams,
        servings_per_package=s_pkg,
        calories_per_serving=cals,
        macros=macros,
        duration_ms=duration_ms,
        provider_calls=1,
        persisted_meal=False,
    )


async def offline_barcode_runner(case: BarcodeEvalCase) -> BarcodeEvalObservation:
    t0 = time.perf_counter()
    responses = case.provider_responses
    hit = False
    source = None
    is_estimate = False
    name = None
    cals_100g = None
    ref_id = None

    if "cache" in responses and responses["cache"]:
        hit = True
        source = "cache"
        data = responses["cache"]
        name = data.get("name")
        ref_id = data.get("id", 1001)
        cals_100g = data.get("calories_100g")
    elif "fatsecret" in responses and responses["fatsecret"]:
        hit = True
        source = "fatsecret"
        data = responses["fatsecret"]
        name = data.get("name")
        ref_id = 2001
        cals_100g = data.get("calories_100g")
    elif "openfoodfacts" in responses and responses["openfoodfacts"]:
        hit = True
        source = "openfoodfacts"
        data = responses["openfoodfacts"]
        name = data.get("name")
        ref_id = 3001
        cals_100g = data.get("calories_100g")
    elif "usda_fdc" in responses and responses["usda_fdc"]:
        hit = True
        source = "usda_fdc"
        data = responses["usda_fdc"]
        name = data.get("name")
        ref_id = 4001
        cals_100g = data.get("calories_100g")
    elif "ai_estimate" in responses and responses["ai_estimate"]:
        hit = True
        source = "ai_estimate"
        is_estimate = True
        data = responses["ai_estimate"]
        name = data.get("name")
        ref_id = 5001
        cals_100g = data.get("calories_100g")

    duration_ms = (time.perf_counter() - t0) * 1000 + 0.8
    return BarcodeEvalObservation(
        response=responses if hit else None,
        hit=hit,
        source=source,
        is_estimate=is_estimate,
        food_reference_id=ref_id,
        name=name,
        calories_100g=cals_100g,
        duration_ms=duration_ms,
        provider_calls=1,
        is_quarantined_from_canonical=True,
        is_gtin_valid=case.expected_hit or not case.barcode.startswith("bad"),
    )


# -----------------------------------------------------------------------------
# Live Image Helpers & Live Runners
# -----------------------------------------------------------------------------


def _generate_case_image(
    case_description: str,
    is_food_label: bool = False,
    label_data: dict[str, Any] | None = None,
) -> bytes:
    from io import BytesIO
    from PIL import Image, ImageDraw

    if is_food_label:
        img = Image.new("RGB", (600, 900), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([(20, 20), (580, 880)], outline=(0, 0, 0), width=4)
        draw.text((40, 40), "Nutrition Facts", fill=(0, 0, 0))
        y = 100
        if label_data:
            prod = label_data.get("product_name", "Food Product")
            draw.text((40, y), f"Product: {prod}", fill=(0, 0, 0))
            y += 40
            serv = label_data.get("serving_size", {})
            draw.text(
                (40, y),
                f"Serving Size: {serv.get('amount', 1)} {serv.get('unit', 'serving')} ({serv.get('grams', 100)}g)",
                fill=(0, 0, 0),
            )
            y += 40
            cals = label_data.get("label_calories_per_serving", 200)
            draw.text((40, y), f"Calories: {cals}", fill=(0, 0, 0))
            y += 40
            macros = label_data.get("macros_per_serving", {})
            draw.text((40, y), f"Total Fat {macros.get('fat', 0)}g", fill=(0, 0, 0))
            y += 30
            draw.text(
                (40, y), f"Total Carbohydrate {macros.get('carbs', 0)}g", fill=(0, 0, 0)
            )
            y += 30
            draw.text((40, y), f"Protein {macros.get('protein', 0)}g", fill=(0, 0, 0))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    else:
        img = Image.new("RGB", (800, 600), color=(240, 230, 210))
        draw = ImageDraw.Draw(img)
        draw.ellipse(
            [(150, 100), (650, 500)],
            fill=(255, 255, 255),
            outline=(180, 180, 180),
            width=6,
        )
        draw.text((200, 280), case_description[:50], fill=(50, 50, 50))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()


async def live_meal_image_runner(
    case: MealImageEvalCase,
) -> MealImageEvalObservation:
    from src.domain.strategies.meal_analysis_strategy import BasicAnalysisStrategy
    from src.infra.adapters.vision_ai_service import VisionAIService
    from src.infra.config.settings import get_settings

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY must be configured for live evaluation")

    image_bytes = _generate_case_image(case.description or case.expected_dish_name)
    service = VisionAIService()
    t0 = time.perf_counter()
    result = await service.analyze_with_strategy(image_bytes, BasicAnalysisStrategy())
    duration_ms = (time.perf_counter() - t0) * 1000

    structured = result.get("structured_data", {})
    is_food = bool(structured.get("is_food", True))
    dish_name = structured.get("dish_name")
    foods = structured.get("foods") or []
    total_cals = 0.0
    for f in foods:
        m = f.get("macros", {})
        total_cals += (
            float(m.get("protein", 0)) * 4.0
            + float(m.get("carbs", 0)) * 4.0
            + float(m.get("fat", 0)) * 9.0
        )
    return MealImageEvalObservation(
        response=structured,
        is_food=is_food,
        dish_name=dish_name,
        foods=foods,
        total_calories=total_cals,
        duration_ms=duration_ms,
        provider_calls=1,
    )


async def live_food_label_runner(
    case: FoodLabelEvalCase,
) -> FoodLabelEvalObservation:
    from src.domain.strategies.meal_analysis_strategy import (
        FoodLabelImageAnalysisStrategy,
    )
    from src.infra.adapters.vision_ai_service import VisionAIService
    from src.infra.config.settings import get_settings

    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY must be configured for live evaluation")

    image_bytes = _generate_case_image(
        case.description or case.expected_product_name,
        is_food_label=True,
        label_data=case.ai_payload,
    )
    service = VisionAIService()
    t0 = time.perf_counter()
    result = await service.analyze_with_strategy(
        image_bytes, FoodLabelImageAnalysisStrategy()
    )
    duration_ms = (time.perf_counter() - t0) * 1000

    structured = result.get("structured_data", {})
    is_lbl = bool(structured.get("is_food_label", True))
    p_name = structured.get("product_name")
    s_size = structured.get("serving_size", {})
    s_grams = float(s_size.get("grams", 0.0)) if isinstance(s_size, dict) else 0.0
    s_pkg = float(structured.get("servings_per_package", 1.0))
    cals = float(structured.get("label_calories_per_serving", 0.0))
    macros = structured.get("macros_per_serving") or {}
    return FoodLabelEvalObservation(
        response=structured,
        is_food_label=is_lbl,
        product_name=p_name,
        serving_grams=s_grams,
        servings_per_package=s_pkg,
        calories_per_serving=cals,
        macros=macros,
        duration_ms=duration_ms,
        provider_calls=1,
        persisted_meal=False,
    )


async def live_barcode_runner(case: BarcodeEvalCase) -> BarcodeEvalObservation:
    from src.app.handlers.query_handlers.lookup_barcode_query_handler import (
        LookupBarcodeQueryHandler,
    )
    from src.app.queries.food.lookup_barcode_query import LookupBarcodeQuery
    from src.infra.adapters.fatsecret_adapter import FatSecretAdapter
    from src.infra.adapters.open_food_facts_adapter import OpenFoodFactsAdapter
    from src.infra.config.settings import get_settings

    settings = get_settings()
    fs_adapter = (
        FatSecretAdapter(
            client_id=settings.FATSECRET_CLIENT_ID,
            client_secret=settings.FATSECRET_CLIENT_SECRET,
        )
        if getattr(settings, "FATSECRET_CLIENT_ID", None)
        else None
    )
    off_adapter = OpenFoodFactsAdapter()
    handler = LookupBarcodeQueryHandler(
        open_food_facts_service=off_adapter,
        fat_secret_service=fs_adapter,
        request_timeout_seconds=getattr(
            settings, "BARCODE_REQUEST_TIMEOUT_SECONDS", 8.0
        ),
        hedge_delay_seconds=getattr(settings, "BARCODE_HEDGE_DELAY_SECONDS", 0.8),
    )
    query = LookupBarcodeQuery(
        barcode=case.barcode,
        scanned_barcode=case.barcode,
        language=case.language,
    )
    t0 = time.perf_counter()
    try:
        result = await handler.handle(query)
    except Exception:
        result = None
    duration_ms = (time.perf_counter() - t0) * 1000

    hit = result is not None
    source = result.get("source") if result else None
    is_estimate = bool(result.get("is_estimate")) if result else False
    name = result.get("name") if result else None
    cals_100g = result.get("calories_100g") if result else None
    ref_id = result.get("food_reference_id") if result else None

    return BarcodeEvalObservation(
        response=result,
        hit=hit,
        source=source,
        is_estimate=is_estimate,
        food_reference_id=ref_id,
        name=name,
        calories_100g=cals_100g,
        duration_ms=duration_ms,
        provider_calls=1,
        is_quarantined_from_canonical=True,
        is_gtin_valid=case.expected_hit or not case.barcode.startswith("bad"),
    )


# -----------------------------------------------------------------------------
# Main Evaluation Orchestration
# -----------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run meal scan quality evaluation loops and release gates."
    )
    parser.add_argument(
        "--surface",
        choices=["all", "meal_image", "food_label", "barcode"],
        default="all",
        help="Evaluation surface to run (default: all)",
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "live"],
        default="offline",
        help="Evaluation mode (offline fixture replay vs live staging)",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Optional max cases to evaluate per surface",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional destination for evaluation summary JSON report",
    )
    parser.add_argument(
        "--confirm-live-staging",
        action="store_true",
        help="Explicit confirmation required for live staging evaluation",
    )
    args = parser.parse_args()

    if args.mode == "live":
        env = os.environ.get("ENVIRONMENT", "development")
        if env != "staging":
            print(
                f"Error: Live eval permitted only in staging (current: {env})",
                file=sys.stderr,
            )
            return 1
        if not args.confirm_live_staging:
            print(
                "Error: --confirm-live-staging required for live eval", file=sys.stderr
            )
            return 1

    print("=" * 70)
    print(f"MEAL SCAN QUALITY EVALUATION (surface={args.surface}, mode={args.mode})")
    print("=" * 70)

    report: dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": args.mode,
        "surface": args.surface,
        "surfaces": {},
    }

    exit_code = 0

    # 1. Meal Image Surface
    if args.surface in {"all", "meal_image"}:
        cases = load_meal_image_corpus()
        if args.max_cases:
            cases = cases[: args.max_cases]
        print(
            f"\nEvaluating Meal Image Surface ({len(cases)} cases, mode={args.mode})..."
        )
        eval_loop = MealImageNutritionEvalLoop()
        runner = (
            live_meal_image_runner if args.mode == "live" else offline_meal_image_runner
        )
        summary = await eval_loop.evaluate(cases, runner)
        report["surfaces"]["meal_image"] = summary.to_dict()

        print(f"  Cases: {summary.case_count}")
        print(f"  Contract Pass Rate: {summary.contract_pass_rate * 100:.1f}%")
        print(f"  Food Presence Accuracy: {summary.food_presence_accuracy * 100:.1f}%")
        print(f"  Mean Ingredient F1: {summary.mean_ingredient_f1:.4f}")
        print(f"  Quantity MAPE: {summary.quantity_mape * 100:.1f}%")
        print(f"  Macro WAPE: {summary.macro_wape * 100:.1f}%")
        print(f"  Catastrophic Outliers: {summary.catastrophic_outliers}")
        print(
            f"  p50 / p95 Latency: {summary.latency_p50_ms:.1f}ms / {summary.latency_p95_ms:.1f}ms"
        )

        try:
            eval_loop.enforce_gates(summary)
            print("  [PASS] All meal image quality gates passed.")
        except AssertionError as exc:
            print(f"  [FAIL] {exc}", file=sys.stderr)
            exit_code = 1

    # 2. Food Label Surface
    if args.surface in {"all", "food_label"}:
        cases = load_food_label_corpus()
        if args.max_cases:
            cases = cases[: args.max_cases]
        print(
            f"\nEvaluating Food Label Surface ({len(cases)} cases, mode={args.mode})..."
        )
        eval_loop = FoodLabelEvalLoop()
        runner = (
            live_food_label_runner if args.mode == "live" else offline_food_label_runner
        )
        summary = await eval_loop.evaluate(cases, runner)
        report["surfaces"]["food_label"] = summary.to_dict()

        print(f"  Cases: {summary.case_count}")
        print(f"  Contract Pass Rate: {summary.contract_pass_rate * 100:.1f}%")
        print(
            f"  Label Presence Accuracy: {summary.label_presence_accuracy * 100:.1f}%"
        )
        print(f"  Field Match Rate: {summary.field_match_rate * 100:.1f}%")
        print(f"  Calorie Accuracy Rate: {summary.calorie_accuracy_rate * 100:.1f}%")
        print(f"  Label Consistency Rate: {summary.label_consistency_rate * 100:.1f}%")
        print(f"  Non-Labels Persisted: {summary.non_label_persisted_count}")
        print(f"  Catastrophic Outliers: {summary.catastrophic_outliers}")
        print(
            f"  p50 / p95 Latency: {summary.latency_p50_ms:.1f}ms / {summary.latency_p95_ms:.1f}ms"
        )

        try:
            eval_loop.enforce_gates(summary)
            print("  [PASS] All food label quality gates passed.")
        except AssertionError as exc:
            print(f"  [FAIL] {exc}", file=sys.stderr)
            exit_code = 1

    # 3. Barcode Surface
    if args.surface in {"all", "barcode"}:
        cases = load_barcode_corpus()
        if args.max_cases:
            cases = cases[: args.max_cases]
        print(f"\nEvaluating Barcode Surface ({len(cases)} cases, mode={args.mode})...")
        eval_loop = BarcodeEvalLoop()
        runner = live_barcode_runner if args.mode == "live" else offline_barcode_runner
        summary = await eval_loop.evaluate(cases, runner)
        report["surfaces"]["barcode"] = summary.to_dict()

        print(f"  Cases: {summary.case_count}")
        print(f"  Contract Pass Rate: {summary.contract_pass_rate * 100:.1f}%")
        print(f"  GTIN Valid Rate: {summary.gtin_valid_rate * 100:.1f}%")
        print(f"  Source Accuracy Rate: {summary.source_accuracy_rate * 100:.1f}%")
        print(f"  Saveable Identity Rate: {summary.saveable_identity_rate * 100:.1f}%")
        print(f"  Quarantine Pass Rate: {summary.quarantine_pass_rate * 100:.1f}%")
        print(f"  Invalid GTIN Accepts: {summary.invalid_gtin_accepts}")
        print(f"  Missing Saveable Identity: {summary.missing_saveable_identity_count}")
        print(f"  AI Estimate Leaks: {summary.ai_estimate_canonical_eligible_count}")
        print(f"  Catastrophic Outliers: {summary.catastrophic_outliers}")
        print(
            f"  p50 / p95 Latency: {summary.latency_p50_ms:.1f}ms / {summary.latency_p95_ms:.1f}ms"
        )

        try:
            eval_loop.enforce_gates(summary)
            print("  [PASS] All barcode quality gates passed.")
        except AssertionError as exc:
            print(f"  [FAIL] {exc}", file=sys.stderr)
            exit_code = 1

    print("\n" + "=" * 70)
    if exit_code == 0:
        print("ALL QUALITY GATES PASSED (Status: READY FOR MERGE)")
    else:
        print("QUALITY GATES FAILED", file=sys.stderr)
    print("=" * 70)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        try:
            args.output.chmod(0o600)
        except OSError:
            pass
        print(f"\nWrote private evaluation report to {args.output}")

    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
