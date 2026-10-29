"""QA-labeled accuracy baseline manifests.

Committed fixtures are the finalized 100 scan and 100 prompt labels. They are
not a copy of the restricted pool: no production meal id, user id, or image URL.
Harness case ids are the QA ids with underscores rewritten to hyphens
(``eval_scan_001`` -> ``eval-scan-001``) because reviewed-manifest ids must match
``^[a-z0-9]+(?:-[a-z0-9]+)+$``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.development.accuracy_baseline_privacy import assert_no_pii

from src.domain.evaluation.prompt_eval_models import MICRO_UNITS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "accuracy_baseline"
SCAN_FIXTURE = FIXTURE_DIR / "meal_scan_scaffold_manifest.json"
TEXT_FIXTURE = FIXTURE_DIR / "parse_text_scaffold_manifest.json"
SCAN_IMAGE_DIR = FIXTURE_DIR / "scan_images"
POOL_PATH = "/workspace/ai-accuracy-eval"
POOL_SQL = "/workspace/ai-accuracy-eval/README.md"
LABELED_CASE_COUNT = 100
HELD_OUT_COUNT = 30
QUALITY_STATUS = "qa_labels_loaded_accuracy_unmeasured_until_live_provider"
CORPUS_KIND = "accuracy_baseline_labeled"
SAMPLE_STATUS = "qa_labeled_exif_stripped"
MANIFEST_LABEL_STATUS = "qa_labeled"
CASE_LABEL_STATUSES = frozenset({"labeled", "partial", "unscored"})
GOLD_SOURCES = frozenset({"label", "db_lookup", "estimate"})
SCAN_FLAGS = frozenset(
    {
        "ambiguous_portion",
        "blurry",
        "label_unreadable",
        "multiple_plates",
        "non_food",
        "packaging_only",
        "partial_plate",
        "poor_lighting",
    }
)
TEXT_FLAGS = frozenset({"ambiguous_portion"})
MACRO_KEYS = ("protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g")
QA_CASE_ID_PATTERN = re.compile(r"^eval_(scan|prompt)_(\d{3})$")
_JPEG_MAGIC = b"\xff\xd8\xff"
_REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "cases",
        "corpus_kind",
        "label_status",
        "privacy_reviewed",
        "review_status",
        "reviewed_by",
        "sample_collection_status",
        "schema_version",
        "source_provenance",
        "surface",
    }
)


@dataclass(frozen=True)
class ScaffoldManifest:
    surface: str
    manifest_sha256: str
    source_provenance: str
    case_count: int
    splits: dict[str, int]
    languages: dict[str, dict[str, int]]
    label_status: str
    privacy_reviewed: bool
    sample_collection_status: str
    quality_evaluation_status: str
    case_ids: tuple[str, ...]
    corpus_gate_status: str
    gold_source_counts: dict[str, int]
    quality_flag_counts: dict[str, int]
    unscored_case_ids: tuple[str, ...]
    partial_case_ids: tuple[str, ...]
    non_food_case_ids: tuple[str, ...]
    macro_case_counts: dict[str, int]
    quantity_labeled_items: int
    quantity_unlabeled_items: int
    known_micro_case_count: int


def scaffold_path(surface: str) -> Path:
    if surface == "meal_scan":
        return SCAN_FIXTURE
    if surface == "parse_text":
        return TEXT_FIXTURE
    raise ValueError(f"unknown scaffold surface {surface!r}")


def held_out_case_ids(case_ids: list[str] | tuple[str, ...]) -> frozenset[str]:
    """Freeze the held-out 30 before any prompt tuning.

    Order is sha256(case_id) hex ascending. The first 30 are held_out.
    """
    ordered = sorted(
        case_ids, key=lambda case_id: hashlib.sha256(case_id.encode()).hexdigest()
    )
    return frozenset(ordered[:HELD_OUT_COUNT])


def harness_case_id(qa_case_id: str, *, surface: str) -> str:
    match = QA_CASE_ID_PATTERN.fullmatch(qa_case_id)
    if match is None:
        raise ValueError(f"{qa_case_id or '<missing>'}: QA case id is not eval_*_NNN")
    kind, number = match.group(1), match.group(2)
    expected_kind = "scan" if surface == "meal_scan" else "prompt"
    if kind != expected_kind:
        raise ValueError(f"{qa_case_id}: does not belong on {surface}")
    return f"eval-{kind}-{number}"


def load_scaffold_manifest(path: str | Path) -> ScaffoldManifest:
    manifest_path = Path(path).expanduser().resolve()
    raw_text = manifest_path.read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise ValueError("labeled manifest must be an object")
    assert_no_pii(raw, label=str(manifest_path.name))
    missing = _REQUIRED_MANIFEST_KEYS - set(raw)
    if missing:
        raise ValueError(f"labeled manifest missing {sorted(missing)}")
    if raw.get("schema_version") != 1:
        raise ValueError("labeled manifest must use schema_version=1")
    if raw.get("corpus_kind") != CORPUS_KIND:
        raise ValueError("labeled corpus_kind is not accuracy_baseline_labeled")
    surface = raw.get("surface")
    if surface not in {"meal_scan", "parse_text"}:
        raise ValueError("labeled surface must be meal_scan or parse_text")
    if raw.get("review_status") != "reviewed":
        raise ValueError("labeled review_status must be reviewed")
    if raw.get("privacy_reviewed") is not True:
        raise ValueError("labeled privacy_reviewed must be true")
    if not str(raw.get("reviewed_by") or "").strip():
        raise ValueError("labeled manifest requires reviewed_by")
    if raw.get("label_status") != MANIFEST_LABEL_STATUS:
        raise ValueError("labeled manifest label_status must be qa_labeled")
    if raw.get("sample_collection_status") != SAMPLE_STATUS:
        raise ValueError("labeled sample_collection_status is not the QA marker")
    if not str(raw.get("source_provenance") or "").strip():
        raise ValueError("labeled manifest requires source_provenance")
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise ValueError("labeled cases must be a list")
    parsed = _validate_case_rows(cases, surface=str(surface), root=manifest_path.parent)
    if len(parsed) != LABELED_CASE_COUNT:
        raise ValueError(
            f"labeled manifest must contain {LABELED_CASE_COUNT} cases, found {len(parsed)}"
        )
    splits, languages = _count_cases(parsed)
    gate = _corpus_gate(str(surface), manifest_path)
    stats = _label_stats(parsed, surface=str(surface))
    return ScaffoldManifest(
        surface=str(surface),
        manifest_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        source_provenance=str(raw["source_provenance"]),
        case_count=len(parsed),
        splits=splits,
        languages=languages,
        label_status=MANIFEST_LABEL_STATUS,
        privacy_reviewed=True,
        sample_collection_status=SAMPLE_STATUS,
        quality_evaluation_status=QUALITY_STATUS,
        case_ids=tuple(case["case_id"] for case in parsed),
        corpus_gate_status=gate,
        **stats,
    )


def _validate_case_rows(
    cases: list[Any], *, surface: str, root: Path
) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in cases:
        if not isinstance(entry, dict):
            raise ValueError("labeled cases must be objects")
        qa_case_id = str(entry.get("qa_case_id") or "")
        case_id = harness_case_id(qa_case_id, surface=surface)
        if entry.get("case_id") != case_id:
            raise ValueError(f"{qa_case_id}: case_id must be {case_id}")
        if case_id in seen:
            raise ValueError(f"{case_id}: duplicate case id")
        seen.add(case_id)
        if entry.get("language") not in {"en", "vi"}:
            raise ValueError(f"{case_id}: language must be en or vi")
        if entry.get("label_status") not in CASE_LABEL_STATUSES:
            raise ValueError(
                f"{case_id}: label_status is not labeled, partial, or unscored"
            )
        if entry.get("gold_source") not in GOLD_SOURCES:
            raise ValueError(f"{case_id}: gold_source is not a QA source")
        flags = entry.get("quality_flags")
        allowed_flags = SCAN_FLAGS if surface == "meal_scan" else TEXT_FLAGS
        if not isinstance(flags, list) or any(
            not isinstance(flag, str) or flag not in allowed_flags for flag in flags
        ):
            raise ValueError(f"{case_id}: quality_flags are not in the QA set")
        _validate_label_status(entry, case_id)
        _validate_micros_present(entry, case_id)
        if surface == "meal_scan":
            _validate_scan_case(
                entry, case_id=case_id, qa_case_id=qa_case_id, root=root
            )
        else:
            _validate_text_case(entry, case_id)
        parsed.append(entry)
    held_out = held_out_case_ids([str(case["case_id"]) for case in parsed])
    for entry in parsed:
        expected_split = "held_out" if entry["case_id"] in held_out else "development"
        if entry.get("split") != expected_split:
            raise ValueError(
                f"{entry['case_id']}: split must be {expected_split} under the frozen hash rule"
            )
    return parsed


def _validate_label_status(entry: dict[str, Any], case_id: str) -> None:
    macros = entry.get("expected_macros") or {}
    if not isinstance(macros, dict):
        raise ValueError(f"{case_id}: expected_macros must be an object")
    present = {"protein_g", "carbs_g", "fat_g"} & set(macros)
    has_kcal = entry.get("expected_calories_kcal") is not None
    status = entry["label_status"]
    if status == "unscored":
        if has_kcal or present:
            raise ValueError(f"{case_id}: unscored cases cannot carry macro gold")
        return
    if not has_kcal:
        raise ValueError(f"{case_id}: {status} cases require expected_calories_kcal")
    if status == "partial" and present == {"protein_g", "carbs_g", "fat_g"}:
        raise ValueError(f"{case_id}: partial cases are missing a macro")
    if status == "labeled" and present != {"protein_g", "carbs_g", "fat_g"}:
        raise ValueError(f"{case_id}: labeled cases require protein, carbs, and fat")
    if status == "partial" and not present:
        raise ValueError(f"{case_id}: partial cases require at least one macro")


def _validate_micros_present(entry: dict[str, Any], case_id: str) -> None:
    micros = entry.get("expected_micros")
    if not isinstance(micros, dict) or set(micros) != set(MICRO_UNITS):
        raise ValueError(
            f"{case_id}: expected_micros must name every supported nutrient"
        )


def _validate_scan_case(
    entry: dict[str, Any], *, case_id: str, qa_case_id: str, root: Path
) -> None:
    image_raw = entry.get("image_path")
    expected_path = f"scan_images/{qa_case_id}.jpg"
    if image_raw != expected_path:
        raise ValueError(f"{case_id}: image_path must be {expected_path}")
    image_path = (root / image_raw).resolve()
    if not image_path.is_relative_to(root.resolve()):
        raise ValueError(
            f"{case_id}: image_path must stay inside the manifest directory"
        )
    if not image_path.is_file():
        raise ValueError(f"{case_id}: reviewed photo is missing")
    image_bytes = image_path.read_bytes()
    if not image_bytes.startswith(_JPEG_MAGIC):
        raise ValueError(f"{case_id}: reviewed photo is not a JPEG")
    if (
        b"Exif\x00\x00" in image_bytes
        or b"http://" in image_bytes
        or b"https://" in image_bytes
    ):
        raise ValueError(f"{case_id}: reviewed photo still contains EXIF or a URL")
    digest = hashlib.sha256(image_bytes).hexdigest()
    if str(entry.get("image_sha256") or "").lower() != digest:
        raise ValueError(f"{case_id}: image_sha256 does not match the file")
    expected_is_food = entry.get("expected_is_food")
    if not isinstance(expected_is_food, bool):
        raise ValueError(f"{case_id}: expected_is_food must be boolean")
    foods = entry.get("expected_foods")
    if not isinstance(foods, list):
        raise ValueError(f"{case_id}: expected_foods must be a list")
    non_food = "non_food" in entry["quality_flags"]
    if non_food != (expected_is_food is False):
        raise ValueError(f"{case_id}: non_food flag must match expected_is_food false")
    if non_food and foods:
        raise ValueError(f"{case_id}: non-food cases cannot include expected_foods")
    recorded = entry.get("recorded_gold_items")
    if non_food:
        if not isinstance(recorded, list) or not recorded:
            raise ValueError(f"{case_id}: non-food cases keep recorded_gold_items")
    elif recorded not in (None, []):
        raise ValueError(f"{case_id}: recorded_gold_items is only for non-food cases")


def _validate_text_case(entry: dict[str, Any], case_id: str) -> None:
    if "image_path" in entry or "image_sha256" in entry:
        raise ValueError(f"{case_id}: text cases cannot carry an image path")
    if not str(entry.get("text") or "").strip():
        raise ValueError(f"{case_id}: reviewed text is required")
    items = entry.get("expected_items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"{case_id}: expected_items are required")


def _corpus_gate(surface: str, path: Path) -> str:
    if surface == "meal_scan":
        from scripts.development.meal_scan_eval_manifest import (
            _corpus_status,
            load_reviewed_manifest,
        )

        return _corpus_status(load_reviewed_manifest(path))
    from scripts.development.parse_text_eval_manifest import (
        _text_corpus_status,
        load_reviewed_provider_manifest,
    )

    cases, splits = load_reviewed_provider_manifest(path)
    return _text_corpus_status(cases, splits)


def _label_stats(cases: list[dict[str, Any]], *, surface: str) -> dict[str, Any]:
    item_key = "expected_foods" if surface == "meal_scan" else "expected_items"
    gold_source_counts: dict[str, int] = {}
    quality_flag_counts: dict[str, int] = {}
    macro_case_counts = dict.fromkeys((*MACRO_KEYS, "expected_calories_kcal"), 0)
    quantity_labeled = 0
    quantity_unlabeled = 0
    known_micro_cases = 0
    unscored: list[str] = []
    partial: list[str] = []
    non_food: list[str] = []
    for case in cases:
        source = str(case["gold_source"])
        gold_source_counts[source] = gold_source_counts.get(source, 0) + 1
        for flag in case["quality_flags"]:
            quality_flag_counts[flag] = quality_flag_counts.get(flag, 0) + 1
        macros = case.get("expected_macros") or {}
        for key in MACRO_KEYS:
            if key in macros:
                macro_case_counts[key] += 1
        if case.get("expected_calories_kcal") is not None:
            macro_case_counts["expected_calories_kcal"] += 1
        if case["label_status"] == "unscored":
            unscored.append(case["case_id"])
        elif case["label_status"] == "partial":
            partial.append(case["case_id"])
        if "non_food" in case["quality_flags"]:
            non_food.append(case["case_id"])
        items = list(case.get(item_key) or [])
        items.extend(case.get("recorded_gold_items") or [])
        for item in items:
            if isinstance(item, dict) and "quantity_g" in item:
                quantity_labeled += 1
            else:
                quantity_unlabeled += 1
        micros = case.get("expected_micros") or {}
        if any(
            isinstance(reference, dict) and reference.get("status") == "known"
            for reference in micros.values()
        ):
            known_micro_cases += 1
    return {
        "gold_source_counts": gold_source_counts,
        "quality_flag_counts": quality_flag_counts,
        "unscored_case_ids": tuple(unscored),
        "partial_case_ids": tuple(partial),
        "non_food_case_ids": tuple(non_food),
        "macro_case_counts": macro_case_counts,
        "quantity_labeled_items": quantity_labeled,
        "quantity_unlabeled_items": quantity_unlabeled,
        "known_micro_case_count": known_micro_cases,
    }


def _count_cases(
    cases: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    splits = {"development": 0, "held_out": 0}
    languages = {
        "development": {"en": 0, "vi": 0},
        "held_out": {"en": 0, "vi": 0},
    }
    for case in cases:
        splits[str(case["split"])] += 1
        languages[str(case["split"])][str(case["language"])] += 1
    return splits, languages
