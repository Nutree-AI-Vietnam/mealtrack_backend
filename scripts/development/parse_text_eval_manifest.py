"""Reviewed text corpus manifest validation and split accounting."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.development.parse_text_eval_references import (
    _validated_macro_references,
    _validated_micro_references,
    _validated_reference,
)

from src.domain.evaluation.meal_text_nutrition_eval_models import (
    TEXT_MACROS,
    TEXT_MICRO_UNITS,
)
from src.domain.model.ai.nutrition_contracts import MAX_TEXT_PARSE_ITEMS
from src.domain.services.meal_text_nutrition_eval_loop import ParseTextEvalCase

CASE_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")


def load_reviewed_provider_manifest(
    path: str | Path,
) -> tuple[list[ParseTextEvalCase], dict[str, str]]:
    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise ValueError(
            "provider-only quality evaluation blocked: reviewed text manifest is missing"
        )
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("reviewed text manifest must use schema_version=1")
    if (
        raw.get("review_status") != "reviewed"
        or not str(raw.get("reviewed_by") or "").strip()
    ):
        raise ValueError(
            "provider-only quality evaluation blocked until reviewed_by and review_status=reviewed"
        )
    if (
        raw.get("privacy_reviewed") is not True
        or not str(raw.get("source_provenance") or "").strip()
    ):
        raise ValueError(
            "reviewed text manifest requires privacy_reviewed=true and source_provenance"
        )
    entries = raw.get("cases")
    if not isinstance(entries, list) or not entries:
        raise ValueError("reviewed text manifest must contain cases")
    cases: list[ParseTextEvalCase] = []
    splits: dict[str, str] = {}
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("reviewed text cases must be objects")
        case_id = str(entry.get("case_id") or "")
        if not CASE_ID_PATTERN.fullmatch(case_id) or case_id in seen:
            raise ValueError("reviewed text IDs must be unique lowercase synthetic IDs")
        seen.add(case_id)
        text = str(entry.get("text") or "").strip()
        if not text:
            raise ValueError(f"{case_id}: reviewed text is required")
        split = entry.get("split")
        if split not in {"development", "held_out"}:
            raise ValueError(f"{case_id}: split must be development or held_out")
        expected_items = entry.get("expected_items")
        if not isinstance(expected_items, list) or not expected_items:
            raise ValueError(f"{case_id}: reviewed expected_items are required")
        if len(expected_items) > MAX_TEXT_PARSE_ITEMS:
            raise ValueError(
                f"{case_id}: expected_items exceed the supported 20-item text contract"
            )
        for item in expected_items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("aliases"), list)
                or not item["aliases"]
                or any(not str(alias).strip() for alias in item["aliases"])
                or "quantity_g" not in item
            ):
                raise ValueError(
                    f"{case_id}: each expected item requires aliases and quantity_g"
                )
            quantity = item["quantity_g"]
            if isinstance(quantity, list):
                if (
                    len(quantity) != 2
                    or float(quantity[0]) <= 0
                    or float(quantity[1]) < float(quantity[0])
                ):
                    raise ValueError(f"{case_id}: item quantity interval is invalid")
            elif float(quantity) <= 0:
                raise ValueError(f"{case_id}: item quantity_g must be positive")
        calories = _validated_reference(
            entry.get("expected_calories_kcal"), f"{case_id}.expected_calories_kcal"
        )
        micros = _validated_micro_references(
            entry.get("expected_micros") or {}, case_id
        )
        macros = _validated_macro_references(
            entry.get("expected_macros") or {}, case_id
        )
        first_item = expected_items[0]
        cases.append(
            ParseTextEvalCase(
                case_id=case_id,
                text=text,
                language=str(entry.get("language") or "en"),
                expected_lookup_name=str(first_item["aliases"][0]),
                expected_quantity_g=float(
                    first_item["quantity_g"]
                    if not isinstance(first_item["quantity_g"], list)
                    else first_item["quantity_g"][0]
                ),
                expected_source="ai_estimate",
                expected_calorie_range=calories,
                expected_items=tuple(expected_items),
                expected_meal_macros=macros,
                expected_meal_micros=micros,
                reference_source_required=False,
            )
        )
        splits[case_id] = split
    return cases, splits


def _text_corpus_status(cases: list[ParseTextEvalCase], splits: dict[str, str]) -> str:
    development = [case for case in cases if splits[case.case_id] == "development"]
    held_out = [case for case in cases if splits[case.case_id] == "held_out"]
    expected = {
        "development": {"en": 10, "vi": 10},
        "held_out": {"en": 5, "vi": 5},
    }
    actual = {
        "development": {
            language: sum(case.language == language for case in development)
            for language in ("en", "vi")
        },
        "held_out": {
            language: sum(case.language == language for case in held_out)
            for language in ("en", "vi")
        },
    }
    if not (
        len(cases) == 30
        and len(development) == 20
        and len(held_out) == 10
        and actual == expected
    ):
        return "blocked_incomplete_reviewed_corpus"

    if any(
        set(case.expected_meal_macros) != set(TEXT_MACROS)
        or set(case.expected_meal_micros) != set(TEXT_MICRO_UNITS)
        for case in cases
    ):
        return "blocked_incomplete_nutrition_references"
    for split in ("development", "held_out"):
        split_cases = [case for case in cases if splits[case.case_id] == split]
        if any(
            not any(
                case.expected_meal_micros[name]["status"] == "known"
                for case in split_cases
            )
            for name in TEXT_MICRO_UNITS
        ):
            return "blocked_incomplete_nutrition_references"
    return "reviewed_corpus_complete"
