"""The full-micros text gold set and the eval's 21-key candidate."""

import importlib
import json
import sys
from pathlib import Path

import pytest

from src.domain.model.ai.full_micros_contracts import (
    FULL_MICROS_ADDED_KEYS,
    MealTextNutritionResponseFull,
)
from src.domain.model.ai.nutrition_contracts import MealTextNutritionResponse

REPO_ROOT = Path(__file__).resolve().parents[3]


def _script(name: str):
    """Import a scripts/development module the way the other script tests do;
    ``tests/unit/scripts`` shadows the top-level ``scripts`` package."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    scripts_package = sys.modules.get("scripts")
    if scripts_package is not None:
        scripts_package.__path__ = [str(REPO_ROOT / "scripts")]
    return importlib.import_module(f"scripts.development.{name}")


build_case = _script("build_full_micros_text_manifest").build_case
_manifest_module = _script("parse_text_eval_manifest")
load_reviewed_provider_manifest = _manifest_module.load_reviewed_provider_manifest
_text_corpus_status = _manifest_module._text_corpus_status
_run_provider_only_case = _script("parse_text_eval_provider")._run_provider_only_case

MANIFEST = REPO_ROOT / "evals" / "full_micros" / "parse_text_nin_foods_manifest.json"


def _copy(tmp_path: Path, **fields) -> Path:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.update(fields)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def _reviewed_copy(tmp_path: Path) -> Path:
    return _copy(tmp_path)


def test_an_unreviewed_gold_set_is_refused(tmp_path):
    draft = _copy(tmp_path, review_status="draft", reviewed_by="")
    with pytest.raises(ValueError, match="review"):
        load_reviewed_provider_manifest(draft)


def test_reviewed_gold_set_is_complete_and_covers_every_new_key(tmp_path):
    cases, splits = load_reviewed_provider_manifest(_reviewed_copy(tmp_path))

    assert len(cases) == 30
    assert _text_corpus_status(cases, splits) == "reviewed_corpus_complete"
    for key in FULL_MICROS_ADDED_KEYS:
        for split in ("development", "held_out"):
            assert any(
                case.expected_meal_micros[key]["status"] == "known"
                for case in cases
                if splits[case.case_id] == split
            ), (key, split)


def _row(category: str, **values) -> dict:
    return {
        "categoryEn": category,
        "name_vi": "Món",
        "energy": 100,
        "nutrition": [{"key": key, "value": value} for key, value in values.items()],
    }


def test_builder_scales_masks_blanks_and_zeroes_only_animal_gaps():
    case = build_case(
        ("7041002", 50, "50g gan lợn luộc", "50g boiled pork liver"),
        _row("Meat and processed products", protein=24.0, **{"vit-b12": 26.0}),
        "development",
        "vi",
    )
    assert case["text"] == "50g gan lợn luộc"
    assert case["expected_macros"]["protein_g"] == 12.0
    assert case["expected_macros"]["fiber_g"] == 0.0
    assert case["expected_micros"]["vitamin_b12"]["value"] == 13.0
    assert case["expected_micros"]["selenium"]["status"] == "masked"
    assert case["expected_micros"]["added_sugar"]["value"] == 0

    with pytest.raises(ValueError, match="plant food"):
        build_case(
            ("5017", 100, "100g đu đủ", "100g papaya"),
            _row("Ripe fruit", protein=1.0),
            "development",
            "en",
        )


class _Generation:
    def __init__(self):
        self.schema = None

    async def generate_meal_plan_async(self, **kwargs):
        self.schema = kwargs["schema"]
        return {
            "items": [
                {
                    "name": "liver",
                    "quantity": 50,
                    "unit": "g",
                    "quantity_g": 50,
                    "macros": {"protein_g": 12, "carbs_g": 1, "fat_g": 2},
                    "micros": {"iron": 9.0, "vitamin_b12": 13.0},
                }
            ]
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("full_micros", [False, True])
async def test_eval_candidate_uses_the_21_key_contract(tmp_path, full_micros):
    cases, _ = load_reviewed_provider_manifest(_reviewed_copy(tmp_path))
    service = _Generation()

    observation = await _run_provider_only_case(
        cases[6], "prompt", service, full_micros=full_micros
    )

    assert service.schema is (
        MealTextNutritionResponseFull if full_micros else MealTextNutritionResponse
    )
    micros = observation.extracted_items[0]["micros"]
    assert ("vitamin_b12" in micros) is full_micros
