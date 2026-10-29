"""Synthetic 100+100 accuracy scaffolds.

These files are placeholders. They are not a trim of the candidate pool at
``/workspace/ai-accuracy-eval`` and they do not repeat that pool's rows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.development.accuracy_baseline_privacy import assert_no_pii
from scripts.development.meal_scan_eval_prompt_bundle import CASE_ID_PATTERN

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "accuracy_baseline"
SCAN_FIXTURE = FIXTURE_DIR / "meal_scan_scaffold_manifest.json"
TEXT_FIXTURE = FIXTURE_DIR / "parse_text_scaffold_manifest.json"
POOL_PATH = "/workspace/ai-accuracy-eval"
POOL_SQL = "/workspace/ai-accuracy-eval/README.md"
SCAFFOLD_CASE_COUNT = 100
QUALITY_STATUS = "blocked_pending_privacy_review_and_qa_labels"

SCAN_SLOTS = (
    "simple-single-food",
    "mixed-plate",
    "vietnamese-mixed-dish",
    "small-topping",
    "soup-or-sauce",
    "occluded-ingredient",
    "visually-similar-foods",
    "non-food-negative",
    "packaging-or-utensil",
    "blur-or-low-light",
    "exif-orientation",
    "cropped-or-partial",
    "known-weight",
    "unconstrained-portion",
    "packed-drink",
)
TEXT_SLOTS = (
    "simple-food",
    "vietnamese-mixed-dish",
    "dry-cooked-pair",
    "egg-fruit-count",
    "liquid-density",
    "exclusion",
    "sauce-or-oil",
    "partial-serving",
    "double-serving",
    "packed-drink",
    "refinement-add",
    "refinement-remove",
    "refinement-quantity",
    "stress-many-items",
    "localization-smoke",
)
_CASE_KEYS = frozenset(
    {
        "case_id",
        "coverage_slot",
        "input_status",
        "label_status",
        "language",
        "language_source",
        "split",
    }
)
_POOL_KEYS = frozenset(
    {
        "loaded_into_this_manifest",
        "path",
        "pool_case_ids",
        "pool_rows",
        "resampled",
        "selection_sql",
        "trim_target",
    }
)
_MANIFEST_KEYS = frozenset(
    {
        "candidate_pool",
        "cases",
        "corpus_kind",
        "label_status",
        "placeholder_notice",
        "privacy_reviewed",
        "review_status",
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


def scaffold_path(surface: str) -> Path:
    if surface == "meal_scan":
        return SCAN_FIXTURE
    if surface == "parse_text":
        return TEXT_FIXTURE
    raise ValueError(f"unknown scaffold surface {surface!r}")


def build_scaffold_payload(surface: str) -> dict[str, Any]:
    """Return the committed placeholder document for one surface."""
    if surface == "meal_scan":
        prefix = "scan"
        slots = SCAN_SLOTS
        pool_case_ids = "eval_scan_001..eval_scan_200"
    elif surface == "parse_text":
        prefix = "text"
        slots = TEXT_SLOTS
        pool_case_ids = "eval_prompt_001..eval_prompt_200"
    else:
        raise ValueError(f"unknown scaffold surface {surface!r}")
    return {
        "schema_version": 1,
        "corpus_kind": "accuracy_baseline_scaffold",
        "surface": surface,
        "review_status": "unreviewed",
        "privacy_reviewed": False,
        "label_status": "pending_qa",
        "sample_collection_status": "placeholders_only",
        "source_provenance": "synthetic_placeholder_no_production_samples",
        "placeholder_notice": (
            "Synthetic placeholder slots only. The 200 plus 200 candidate pool "
            "already exists at /workspace/ai-accuracy-eval and is not copied "
            "here. These case ids are not a trim of that pool."
        ),
        "candidate_pool": {
            "path": POOL_PATH,
            "selection_sql": POOL_SQL,
            "resampled": False,
            "loaded_into_this_manifest": False,
            "pool_rows": 200,
            "pool_case_ids": pool_case_ids,
            "trim_target": 100,
        },
        "cases": _scaffold_cases(prefix, slots),
    }


def _scaffold_cases(prefix: str, slots: tuple[str, ...]) -> list[dict[str, str]]:
    counters = {"en-dev": 0, "vi-dev": 0, "en-hold": 0, "vi-hold": 0}
    cases: list[dict[str, str]] = []
    for index in range(SCAFFOLD_CASE_COUNT):
        language = "en" if index % 2 == 0 else "vi"
        split = "development" if index < 70 else "held_out"
        token = "dev" if split == "development" else "hold"
        bucket = f"{language}-{token}"
        counters[bucket] += 1
        cases.append(
            {
                "case_id": f"{prefix}-{language}-{token}-{counters[bucket]:03d}",
                "language": language,
                "split": split,
                "label_status": "pending_qa",
                "input_status": "placeholder_no_sample",
                "coverage_slot": slots[index % len(slots)],
                "language_source": "scaffold_slot_not_from_production",
            }
        )
    return cases


def write_committed_scaffolds() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for surface, path in (("meal_scan", SCAN_FIXTURE), ("parse_text", TEXT_FIXTURE)):
        payload = build_scaffold_payload(surface)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def load_scaffold_manifest(path: str | Path) -> ScaffoldManifest:
    manifest_path = Path(path).expanduser().resolve()
    raw_text = manifest_path.read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise ValueError("scaffold manifest must be an object")
    assert_no_pii(raw, label=str(manifest_path.name))
    _require_exact_keys(raw, _MANIFEST_KEYS, "scaffold manifest")
    if raw.get("schema_version") != 1:
        raise ValueError("scaffold manifest must use schema_version=1")
    if raw.get("corpus_kind") != "accuracy_baseline_scaffold":
        raise ValueError("scaffold corpus_kind is not accuracy_baseline_scaffold")
    surface = raw.get("surface")
    if surface not in {"meal_scan", "parse_text"}:
        raise ValueError("scaffold surface must be meal_scan or parse_text")
    if raw.get("review_status") != "unreviewed":
        raise ValueError("scaffold review_status must stay unreviewed")
    if raw.get("privacy_reviewed") is not False:
        raise ValueError("scaffold privacy_reviewed must stay false")
    if raw.get("label_status") != "pending_qa":
        raise ValueError("scaffold label_status must stay pending_qa")
    if raw.get("sample_collection_status") != "placeholders_only":
        raise ValueError("scaffold must stay placeholders_only")
    if raw.get("source_provenance") != "synthetic_placeholder_no_production_samples":
        raise ValueError("scaffold source_provenance is not the synthetic marker")
    _validate_pool_pointer(raw.get("candidate_pool"))
    slots = SCAN_SLOTS if surface == "meal_scan" else TEXT_SLOTS
    prefix = "scan" if surface == "meal_scan" else "text"
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise ValueError("scaffold cases must be a list")
    parsed = _validate_cases(cases, prefix=prefix, slots=slots)
    splits, languages = _count_cases(parsed)
    expected_languages = {
        "development": {"en": 35, "vi": 35},
        "held_out": {"en": 15, "vi": 15},
    }
    if (
        len(parsed) != SCAFFOLD_CASE_COUNT
        or splits != {"development": 70, "held_out": 30}
        or languages != expected_languages
    ):
        raise ValueError(
            "scaffold must be 100 cases: 70 development and 30 held_out, balanced en/vi"
        )
    return ScaffoldManifest(
        surface=str(surface),
        manifest_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        source_provenance=str(raw["source_provenance"]),
        case_count=len(parsed),
        splits=splits,
        languages=languages,
        label_status="pending_qa",
        privacy_reviewed=False,
        sample_collection_status="placeholders_only",
        quality_evaluation_status=QUALITY_STATUS,
        case_ids=tuple(case["case_id"] for case in parsed),
    )


def _validate_pool_pointer(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("scaffold candidate_pool must be an object")
    _require_exact_keys(raw, _POOL_KEYS, "candidate_pool")
    if raw.get("path") != POOL_PATH or raw.get("selection_sql") != POOL_SQL:
        raise ValueError("scaffold must point at the existing candidate pool")
    if raw.get("resampled") is not False:
        raise ValueError("scaffold must not record a new sample")
    if raw.get("loaded_into_this_manifest") is not False:
        raise ValueError("production pool rows must not be loaded into fixtures")
    if raw.get("pool_rows") != 200 or raw.get("trim_target") != 100:
        raise ValueError("scaffold pool pointer must stay 200 rows trimming to 100")
    if not isinstance(raw.get("pool_case_ids"), str) or not raw["pool_case_ids"]:
        raise ValueError("scaffold pool_case_ids must name the existing ids")


def _validate_cases(
    cases: list[Any], *, prefix: str, slots: tuple[str, ...]
) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in cases:
        if not isinstance(entry, dict):
            raise ValueError("scaffold cases must be objects")
        _require_exact_keys(entry, _CASE_KEYS, "scaffold case")
        case_id = str(entry.get("case_id") or "")
        if not CASE_ID_PATTERN.fullmatch(case_id) or not case_id.startswith(
            f"{prefix}-"
        ):
            raise ValueError(f"{case_id or '<missing>'}: synthetic case id required")
        if case_id in seen:
            raise ValueError(f"{case_id}: duplicate scaffold case id")
        seen.add(case_id)
        if entry.get("language") not in {"en", "vi"}:
            raise ValueError(f"{case_id}: language must be en or vi")
        if entry.get("split") not in {"development", "held_out"}:
            raise ValueError(f"{case_id}: split must be development or held_out")
        if entry.get("label_status") != "pending_qa":
            raise ValueError(f"{case_id}: label_status must stay pending_qa")
        if entry.get("input_status") != "placeholder_no_sample":
            raise ValueError(f"{case_id}: input_status must stay placeholder_no_sample")
        if entry.get("language_source") != "scaffold_slot_not_from_production":
            raise ValueError(f"{case_id}: language_source is not a production label")
        if entry.get("coverage_slot") not in slots:
            raise ValueError(f"{case_id}: coverage_slot is not in the scaffold set")
        parsed.append({key: str(entry[key]) for key in _CASE_KEYS})
    return parsed


def _count_cases(
    cases: list[dict[str, str]],
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    splits = {"development": 0, "held_out": 0}
    languages = {
        "development": {"en": 0, "vi": 0},
        "held_out": {"en": 0, "vi": 0},
    }
    for case in cases:
        splits[case["split"]] += 1
        languages[case["split"]][case["language"]] += 1
    return splits, languages


def _require_exact_keys(
    raw: dict[str, Any], allowed: frozenset[str], label: str
) -> None:
    extra = set(raw) - allowed
    missing = allowed - set(raw)
    if extra or missing:
        raise ValueError(f"{label} keys must be exactly {sorted(allowed)}")
