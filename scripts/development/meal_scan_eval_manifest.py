"""Validation for reviewed, privacy-approved meal-photo references."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.development.meal_scan_eval_prompt_bundle import (
    CASE_ID_PATTERN,
    ReviewedMealCase,
)
from scripts.development.meal_scan_eval_references import (
    _interval,
    _validate_expected_food,
    _validate_macro_refs,
    _validate_micro_refs,
)

from src.domain.services.meal_analysis.prompt_eval_loop import (
    MACRO_FIELDS,
    MICRO_UNITS,
    PromptEvalCase,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_reviewed_manifest(path: str | Path) -> list[ReviewedMealCase]:
    manifest_path = Path(path).expanduser().resolve()
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("reviewed photo manifest must use schema_version=1")
    if raw.get("review_status") != "reviewed":
        raise ValueError("photo corpus is blocked until review_status is reviewed")
    if not str(raw.get("reviewed_by") or "").strip():
        raise ValueError("reviewed photo manifest requires reviewed_by")
    if raw.get("privacy_reviewed") is not True:
        raise ValueError("photo corpus requires privacy_reviewed=true")
    if not str(raw.get("source_provenance") or "").strip():
        raise ValueError("photo corpus requires source_provenance")
    entries = raw.get("cases")
    if not isinstance(entries, list) or not entries:
        raise ValueError("reviewed photo manifest must contain cases")

    cases: list[ReviewedMealCase] = []
    seen: set[str] = set()
    root = manifest_path.parent.resolve()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("photo manifest cases must be objects")
        case_id = str(entry.get("case_id") or "")
        if not CASE_ID_PATTERN.fullmatch(case_id) or case_id in seen:
            raise ValueError(
                "photo manifest IDs must be unique lowercase synthetic IDs"
            )
        seen.add(case_id)
        image_raw = entry.get("image_path")
        if not isinstance(image_raw, str) or not image_raw:
            raise ValueError(f"{case_id}: image_path is required")
        image_path = (root / image_raw).resolve()
        if not image_path.is_relative_to(root):
            raise ValueError(
                f"{case_id}: image_path must stay inside the manifest directory"
            )
        if not image_path.is_file():
            raise ValueError(f"{case_id}: reviewed photo is missing")
        image_bytes = image_path.read_bytes()
        expected_digest = str(entry.get("image_sha256") or "").lower()
        actual_digest = hashlib.sha256(image_bytes).hexdigest()
        if expected_digest != actual_digest:
            raise ValueError(f"{case_id}: image_sha256 does not match the file")
        if entry.get("split") not in {"development", "held_out"}:
            raise ValueError(f"{case_id}: split must be development or held_out")
        if not isinstance(entry.get("expected_foods"), list):
            raise ValueError(f"{case_id}: expected_foods must be a reviewed list")
        expected_foods = tuple(
            _validate_expected_food(case_id, food) for food in entry["expected_foods"]
        )
        expected_is_food = entry.get("expected_is_food", bool(expected_foods))
        if not isinstance(expected_is_food, bool):
            raise ValueError(f"{case_id}: expected_is_food must be boolean")
        if expected_is_food and not expected_foods:
            raise ValueError(
                f"{case_id}: food photos require at least one expected food"
            )
        if not expected_is_food and expected_foods:
            raise ValueError(f"{case_id}: non-food cases cannot include expected_foods")
        calorie_value = entry.get("expected_calories_kcal")
        expected_calories = (
            _interval(calorie_value, f"{case_id}.expected_calories_kcal")
            if calorie_value is not None
            else None
        )
        if expected_is_food and expected_calories is None:
            raise ValueError(f"{case_id}: food cases require expected_calories_kcal")
        expected_macros = _validate_macro_refs(
            entry.get("expected_macros", {}), case_id
        )
        expected_micros = _validate_micro_refs(
            entry.get("expected_micros", {}), case_id
        )
        evaluation = PromptEvalCase(
            case_id=case_id,
            expected_foods=expected_foods,
            expected_calories_kcal=expected_calories,
            expected_macros=expected_macros,
            expected_micros=expected_micros,
            expected_is_food=expected_is_food,
        )
        cases.append(
            ReviewedMealCase(
                evaluation,
                str(entry.get("language") or "en"),
                image_path,
                str(entry["split"]),
            )
        )
    return cases


def _corpus_status(cases: list[ReviewedMealCase]) -> str:
    development = [case for case in cases if case.split == "development"]
    held_out = [case for case in cases if case.split == "held_out"]
    expected_split_languages = {
        "development": {"en": 10, "vi": 10},
        "held_out": {"en": 5, "vi": 5},
    }
    split_languages = {
        split: {
            language: sum(case.language == language for case in rows)
            for language in ("en", "vi")
        }
        for split, rows in (("development", development), ("held_out", held_out))
    }
    shape_complete = (
        len(cases) == 30
        and len(development) == 20
        and len(held_out) == 10
        and split_languages == expected_split_languages
    )
    if not shape_complete:
        return "blocked_incomplete_reviewed_corpus"

    food_cases = [case for case in cases if case.evaluation.expected_is_food]
    if not food_cases or any(
        set(case.evaluation.expected_macros) != set(MACRO_FIELDS)
        or set(case.evaluation.expected_micros) != set(MICRO_UNITS)
        for case in food_cases
    ):
        return "blocked_incomplete_nutrition_references"

    for split in ("development", "held_out"):
        split_food_cases = [case for case in food_cases if case.split == split]
        if any(
            not any(
                case.evaluation.expected_micros[name]["status"] == "known"
                for case in split_food_cases
            )
            for name in MICRO_UNITS
        ):
            return "blocked_incomplete_nutrition_references"
    return "reviewed_corpus_complete"
