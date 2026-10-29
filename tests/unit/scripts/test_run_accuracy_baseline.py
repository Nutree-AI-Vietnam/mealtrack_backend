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


def test_scaffolds_are_synthetic_placeholders_not_the_pool():
    module = _load_harness()
    scaffold = _scaffold()
    scan = module.load_scan_scaffold_manifest()
    text = module.load_text_scaffold_manifest()
    assert json.loads(scaffold.SCAN_FIXTURE.read_text()) == (
        scaffold.build_scaffold_payload("meal_scan")
    )
    assert json.loads(scaffold.TEXT_FIXTURE.read_text()) == (
        scaffold.build_scaffold_payload("parse_text")
    )

    assert scan.case_count == 100
    assert text.case_count == 100
    assert scan.privacy_reviewed is False
    assert text.sample_collection_status == "placeholders_only"
    assert scan.quality_evaluation_status == module.QUALITY_STATUS
    assert scan.splits == {"development": 70, "held_out": 30}
    assert scan.languages["development"] == {"en": 35, "vi": 35}
    assert text.languages["held_out"] == {"en": 15, "vi": 15}
    assert scan.case_ids[0] == "scan-en-dev-001"
    assert text.case_ids[-1] == "text-vi-hold-015"
    assert "eval_scan_001" not in scan.case_ids
    assert "eval_prompt_001" not in text.case_ids
    blob = (
        scaffold.SCAN_FIXTURE.read_text() + scaffold.TEXT_FIXTURE.read_text()
    ).lower()
    for forbidden in ("user_id", "meal_id", "http://", "https://", "@"):
        assert forbidden not in blob


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
    assert first["candidate_pool"]["path"] == "/workspace/ai-accuracy-eval"
    assert first["candidate_pool"]["selection_sql"].endswith("README.md")
    assert first["scan"]["contract"]["mode"] == "offline_contract_only"
    assert first["scan"]["contract"]["parse_success_rate"] == 1.0
    assert first["scan"]["contract"]["validation_success_rate"] == 1.0
    assert first["scan"]["contract"]["prompt_accuracy"] is None
    assert first["scan"]["scaffold"]["case_count"] == 100
    assert first["text"]["scaffold"]["case_count"] == 100
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
