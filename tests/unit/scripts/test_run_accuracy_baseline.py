import importlib.util
import json
import sys
from pathlib import Path

import pytest

from src.domain.evaluation.meal_text_nutrition_eval_models import (
    TEXT_MACROS,
    TEXT_MICRO_UNITS,
)
from src.domain.evaluation.prompt_eval_models import MACRO_FIELDS, MICRO_UNITS

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = (
    REPOSITORY_ROOT / "scripts" / "development" / "accuracy_baseline_label_schema.json"
)
CHECKLIST_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "development"
    / "accuracy_baseline_privacy_checklist.md"
)


def _load_harness():
    if str(REPOSITORY_ROOT) not in sys.path:
        sys.path.insert(0, str(REPOSITORY_ROOT))
    scripts_package = sys.modules.get("scripts")
    if scripts_package is not None:
        scripts_package.__path__ = [str(REPOSITORY_ROOT / "scripts")]
    for name in tuple(sys.modules):
        if name.startswith("scripts.development"):
            del sys.modules[name]
    path = REPOSITORY_ROOT / "scripts/development/run_accuracy_baseline.py"
    spec = importlib.util.spec_from_file_location("run_accuracy_baseline", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scaffold():
    return sys.modules["scripts.development.accuracy_baseline_scaffold"]


def test_labeled_fixtures_load_and_keep_qa_case_ids():
    module = _load_harness()
    scaffold = _scaffold()
    scan_manifest_module = sys.modules["scripts.development.meal_scan_eval_manifest"]
    text_manifest_module = sys.modules["scripts.development.parse_text_eval_manifest"]
    scan = module.load_scan_scaffold_manifest()
    text = module.load_text_scaffold_manifest()
    scan_cases = scan_manifest_module.load_reviewed_manifest(scaffold.SCAN_FIXTURE)
    text_cases, _splits = text_manifest_module.load_reviewed_provider_manifest(
        scaffold.TEXT_FIXTURE
    )

    assert scan.case_count == text.case_count == 100
    assert len(scan_cases) == len(text_cases) == 100
    assert scan.privacy_reviewed is True
    assert text.privacy_reviewed is True
    assert scan.label_status == text.label_status == "qa_labeled"
    assert scan.sample_collection_status == "qa_labeled_exif_stripped"
    assert scan.quality_evaluation_status == module.QUALITY_STATUS
    assert scan.splits == text.splits == {"development": 70, "held_out": 30}
    assert scan.case_ids[0] == "eval-scan-001"
    assert "eval-scan-196" in scan.unscored_case_ids
    assert scan.partial_case_ids == ("eval-scan-113",)
    assert scan.non_food_case_ids == ("eval-scan-144",)
    assert text.unscored_case_ids == ("eval-prompt-121",)
    assert scan.gold_source_counts == {"db_lookup": 20, "estimate": 64, "label": 16}
    assert text.gold_source_counts == {"db_lookup": 60, "estimate": 40}
    assert scan.macro_case_counts["fiber_g"] == 0
    assert text.macro_case_counts["sugar_g"] == 0
    assert scan.known_micro_case_count == text.known_micro_case_count == 0
    assert scan.corpus_gate_status == "blocked_incomplete_reviewed_corpus"
    assert "eval_scan_001" not in scan.case_ids
    assert "eval_prompt_003" not in text.case_ids
    raw_scan = json.loads(scaffold.SCAN_FIXTURE.read_text())
    raw_text = json.loads(scaffold.TEXT_FIXTURE.read_text())
    for case in raw_scan["cases"]:
        assert case["case_id"] == case["qa_case_id"].replace("_", "-")
        assert case["image_path"] == f"scan_images/{case['qa_case_id']}.jpg"
        assert not Path(case["image_path"]).is_absolute()
    for case in raw_text["cases"]:
        assert case["case_id"] == case["qa_case_id"].replace("_", "-")
        assert "image_path" not in case
    blob = (
        scaffold.SCAN_FIXTURE.read_text() + scaffold.TEXT_FIXTURE.read_text()
    ).lower()
    for forbidden in ("user_id", "meal_id", "http://", "https://", "@"):
        assert forbidden not in blob
    for image in scaffold.SCAN_IMAGE_DIR.glob("*.jpg"):
        data = image.read_bytes()
        assert data.startswith(b"\xff\xd8\xff")
        for forbidden in (
            b"user_id",
            b"meal_id",
            b"http://",
            b"https://",
            b"Exif\x00\x00",
        ):
            assert forbidden not in data


def test_labeled_loader_rejects_unreviewed_privacy_flag(tmp_path):
    _load_harness()
    scaffold = _scaffold()
    payload = json.loads(scaffold.SCAN_FIXTURE.read_text())
    payload["privacy_reviewed"] = False
    path = tmp_path / "unreviewed.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="privacy_reviewed must be true"):
        scaffold.load_scaffold_manifest(path)


def test_scaffold_rejects_production_identifiers(tmp_path):
    _load_harness()
    scaffold = _scaffold()
    payload = json.loads(scaffold.SCAN_FIXTURE.read_text())
    payload["cases"][0]["meal_id"] = "not-used"
    path = tmp_path / "leaky.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="forbidden key meal_id"):
        scaffold.load_scaffold_manifest(path)


def test_baseline_report_is_reproducible_and_does_not_claim_accuracy():
    module = _load_harness()

    first = module.build_baseline_report()
    second = module.build_baseline_report()

    assert first == second
    assert module.render_report(first) == module.render_report(second)
    assert first["schema_version"] == 1
    assert first["accuracy_claim"] is None
    assert first["candidate_quality_measured"] is False
    assert first["provider_calls"] == 0
    assert first["grounding_arm"]["implemented"] is False
    assert first["grounding_arm"]["requested"] == "not-implemented"
    assert first["candidate_pool"]["resampled"] is False
    assert first["candidate_pool"]["loaded_into_fixtures"] is False
    assert first["candidate_pool"]["qa_labeled_fixtures_loaded"] is True
    assert first["candidate_pool"]["status"] == (
        "qa_labels_committed_production_ids_not_copied"
    )
    assert first["candidate_pool"]["path"] == "/workspace/ai-accuracy-eval"
    assert first["candidate_pool"]["selection_sql"].endswith("README.md")
    assert first["scan"]["contract"]["mode"] == "offline_contract_only"
    assert first["scan"]["contract"]["parse_success_rate"] == 1.0
    assert first["scan"]["contract"]["validation_success_rate"] == 1.0
    assert first["scan"]["contract"]["prompt_accuracy"] is None
    assert first["scan"]["contract"]["quality_evaluation_status"] == (
        "contract_only_labeled_photos_not_scored"
    )
    assert first["scan"]["scaffold"]["case_count"] == 100
    assert first["scan"]["scaffold"]["privacy_reviewed"] is True
    assert first["text"]["scaffold"]["case_count"] == 100
    assert first["text"]["scaffold"]["privacy_reviewed"] is True
    assert first["quality_evaluation_status"] == module.QUALITY_STATUS
    assert first["text"]["offline_contract"]["contract_pass_rate"] == 1.0
    assert first["text"]["offline_contract"]["quality_metrics"] is None
    assert "latency" not in json.dumps(first["text"]["offline_contract"])
    rendered = module.render_report(first)
    for forbidden in ("user_id", "meal_id", "https://", "http://"):
        assert forbidden not in rendered


def test_cli_writes_the_same_report(tmp_path, capsys):
    module = _load_harness()
    output = tmp_path / "baseline.json"

    assert module.main(["--output", str(output)]) == 0

    written = json.loads(output.read_text())
    captured = capsys.readouterr()
    assert "mode=accuracy_baseline" in captured.out
    assert written["report_kind"] == "accuracy_baseline"
    assert written["scan"]["scaffold"]["case_count"] == 100


def test_grounding_on_is_rejected():
    module = _load_harness()

    with pytest.raises(SystemExit):
        module.main(["--grounding", "on"])


def test_label_schema_matches_eval_nutrients_and_checklist_points_at_pool():
    schema = json.loads(SCHEMA_PATH.read_text())
    scan_macros = schema["surfaces"]["meal_scan"]["qa_fields"]["expected_macros"]
    text_micros = schema["surfaces"]["parse_text"]["qa_fields"]["expected_micros"]

    assert set(scan_macros) == set(MACRO_FIELDS) == set(TEXT_MACROS)
    assert (
        {name: item["unit"] for name, item in text_micros.items()}
        == MICRO_UNITS
        == TEXT_MICRO_UNITS
    )
    assert "meal_id" in schema["forbidden_fields"]
    assert "user_id" in schema["forbidden_fields"]
    checklist = CHECKLIST_PATH.read_text()
    assert "/workspace/ai-accuracy-eval/" in checklist
    assert "/workspace/ai-accuracy-eval/README.md" in checklist
    assert "Do not re-sample" in checklist
    assert "billowing-sea-41604960" in checklist
    assert "qa_label_schema.json" in checklist
    assert "AI_MEAL_SCAN_REFERENCE_GROUNDING_ENABLED" in checklist
    assert "python scripts/development/run_accuracy_baseline.py" in checklist
