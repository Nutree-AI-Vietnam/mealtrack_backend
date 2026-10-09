import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from src.domain.services.meal_analysis.prompt_eval_loop import PromptEvalResult


def _load_module():
    repository_root = Path(__file__).resolve().parents[3]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    scripts_package = sys.modules.get("scripts")
    if scripts_package is not None:
        scripts_package.__path__ = [str(repository_root / "scripts")]
    for name in tuple(sys.modules):
        if name.startswith("scripts.development"):
            del sys.modules[name]
    script_path = (
        repository_root
        / "scripts"
        / "development"
        / "evaluate_meal_analyze_prompt_candidates.py"
    )
    spec = importlib.util.spec_from_file_location("evaluate_meal_analyze", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_resolve_gate_candidate_prefers_runtime_selected_candidate():
    module = _load_module()
    ranked = [
        PromptEvalResult(
            name="legacy",
            parse_success_rate=1.0,
            validation_success_rate=1.0,
            prompt_tokens_estimate=180.0,
            score=98.2,
        ),
        PromptEvalResult(
            name="optimized",
            parse_success_rate=1.0,
            validation_success_rate=1.0,
            prompt_tokens_estimate=220.0,
            score=97.8,
        ),
    ]

    chosen = module.resolve_gate_candidate(ranked, runtime_candidate_name="optimized")

    assert chosen.name == "optimized"


def test_provider_mode_requires_staging_and_explicit_confirmation(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    with pytest.raises(RuntimeError, match="ENVIRONMENT=staging"):
        module.assert_provider_staging_allowed(True)

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="ENVIRONMENT=staging"):
        module.assert_provider_staging_allowed(True)

    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(RuntimeError, match="confirm-provider-evaluation"):
        module.assert_provider_staging_allowed(False)

    module.assert_provider_staging_allowed(True)

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert (
        module.main(
            [
                "--mode",
                "provider",
                "--manifest",
                "missing-manifest.json",
                "--baseline-prompt",
                "missing-baseline.json",
                "--candidate-prompt",
                "missing-candidate.json",
                "--confirm-provider-evaluation",
            ]
        )
        == 1
    )


def test_contract_mode_report_does_not_claim_prompt_quality(tmp_path):
    module = _load_module()
    report_path = tmp_path / "contract.json"

    assert module.main(["--mode", "contract", "--output", str(report_path)]) == 0

    report = json.loads(report_path.read_text())
    assert report["mode"] == "offline_contract_only"
    assert report["candidate_quality_measured"] is False
    assert report["quality_evaluation_status"] == "blocked_no_reviewed_photo_corpus"
    assert report["prompt_accuracy"] is None


def test_provider_batch_offset_selects_later_bounded_cases():
    module = _load_module()
    cases = list(range(30))

    admitted, selected = module.select_provider_batch(
        cases, repetitions=2, max_cases=25, case_offset=7
    )

    assert admitted == 12  # 50-generation cap / 2 candidates / 2 repetitions
    assert selected == cases[7:19]
    with pytest.raises(ValueError, match="non-negative"):
        module.select_provider_batch(cases, 1, 10, case_offset=-1)


def test_prompt_bundle_accepts_validated_localized_snapshot(tmp_path):
    module = _load_module()
    prompt_en = "scan prompt en"
    prompt_vi = "scan prompt vi"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "prompts": {
                    "meal_scan:en": {
                        "text": prompt_en,
                        "sha256": hashlib.sha256(prompt_en.encode()).hexdigest(),
                    },
                    "meal_scan:vi": {
                        "text": prompt_vi,
                        "sha256": hashlib.sha256(prompt_vi.encode()).hexdigest(),
                    },
                    "parse_text:en": {"text": "other family", "sha256": "ignored"},
                }
            }
        )
    )

    bundle, _ = module.load_prompt_bundle(snapshot, family="meal_scan")

    assert bundle == {"en": prompt_en, "vi": prompt_vi}
    assert set(module.rendered_prompt_hashes(bundle, {"en", "vi"})) == {"en", "vi"}


def test_reviewed_photo_manifest_validates_hash_and_reports_incomplete_corpus(tmp_path):
    module = _load_module()
    image = tmp_path / "meal.jpg"
    image.write_bytes(b"reviewed image bytes")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus_version": "reviewed-test-only",
                "review_status": "reviewed",
                "reviewed_by": "reviewer",
                "privacy_reviewed": True,
                "source_provenance": "documented source",
                "cases": [
                    {
                        "case_id": "meal-case-1",
                        "image_path": image.name,
                        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                        "split": "development",
                        "language": "en",
                        "expected_foods": [
                            {"aliases": ["rice"], "quantity_g": [90, 110]}
                        ],
                        "expected_calories_kcal": [100, 200],
                        "expected_macros": {"carbs_g": [20, 40]},
                        "expected_micros": {
                            "vitamin_a": {
                                "status": "known",
                                "unit": "mcg",
                                "value": [1, 2],
                            }
                        },
                    }
                ],
            }
        )
    )

    cases = module.load_reviewed_manifest(manifest)

    assert len(cases) == 1
    assert cases[0].evaluation.expected_foods[0]["aliases"] == ["rice"]
    assert module._corpus_status(cases) == "blocked_incomplete_reviewed_corpus"


def test_balanced_photo_corpus_needs_all_macro_and_micro_references():
    from types import SimpleNamespace

    from src.domain.services.meal_analysis.prompt_eval_loop import (
        MACRO_FIELDS,
        MICRO_UNITS,
    )

    module = _load_module()

    def make_cases(include_references):
        cases = []
        for split, rows in (("development", 20), ("held_out", 10)):
            for index in range(rows):
                micros = (
                    {
                        name: {"status": "known", "unit": unit, "value": 1.0}
                        for name, unit in MICRO_UNITS.items()
                    }
                    if include_references
                    else {}
                )
                evaluation = SimpleNamespace(
                    expected_is_food=True,
                    expected_macros=(
                        dict.fromkeys(MACRO_FIELDS, (1.0, 2.0))
                        if include_references
                        else {}
                    ),
                    expected_micros=micros,
                )
                cases.append(
                    SimpleNamespace(
                        split=split,
                        language="en" if index % 2 == 0 else "vi",
                        evaluation=evaluation,
                    )
                )
        return cases

    assert module._corpus_status(make_cases(True)) == "reviewed_corpus_complete"
    assert (
        module._corpus_status(make_cases(False))
        == "blocked_incomplete_nutrition_references"
    )
