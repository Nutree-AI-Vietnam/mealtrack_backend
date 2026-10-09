import asyncio
import hashlib
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


def _module():
    repository_root = Path(__file__).resolve().parents[3]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    scripts_package = sys.modules.get("scripts")
    if scripts_package is not None:
        scripts_package.__path__ = [str(repository_root / "scripts")]
    for name in tuple(sys.modules):
        if name.startswith("scripts.development"):
            del sys.modules[name]
    path = repository_root / "scripts/development/evaluate_parse_text_nutrition.py"
    spec = importlib.util.spec_from_file_location("parse_text_eval_script", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_golden_corpus_is_synthetic_and_versioned():
    cases, drop_cases = _module().load_corpus()
    assert len(cases) + len(drop_cases) >= 10
    assert all("@" not in case.case_id for case in (*cases, *drop_cases))
    assert any(case.case_id == "vi-potato-raw-100g" for case in cases)
    assert any(case.expected_source == "custom" for case in cases)


def test_live_mode_fails_closed_for_unset_or_non_staging_environment(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("PARSE_TEXT_LIVE_EVAL_ENABLED", raising=False)

    with pytest.raises(RuntimeError, match="ENVIRONMENT=staging"):
        _module().assert_live_staging_allowed(True)

    monkeypatch.setenv("ENVIRONMENT", "staging")
    with pytest.raises(RuntimeError, match="PARSE_TEXT_LIVE_EVAL_ENABLED"):
        _module().assert_live_staging_allowed(True)

    monkeypatch.setenv("PARSE_TEXT_LIVE_EVAL_ENABLED", "true")
    with pytest.raises(RuntimeError, match="confirm-live-staging"):
        _module().assert_live_staging_allowed(False)


def test_live_mode_rejects_production_even_when_enabled(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PARSE_TEXT_LIVE_EVAL_ENABLED", "true")

    with pytest.raises(RuntimeError, match="ENVIRONMENT=staging"):
        _module().assert_live_staging_allowed(True)


def test_offline_cli_writes_private_non_raw_report_without_overwrite(tmp_path, capsys):
    output = tmp_path / "eval.json"
    assert _module().main(["--mode", "offline", "--output", str(output)]) == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    report = json.loads(output.read_text())
    serialized = output.read_text()
    assert report["case_count"] >= 10
    assert report["offline_fixture_latency_p50_ms"] is not None
    assert report["provider_generation_latency_p50_ms"] is None
    assert "100gr khoai tay" not in serialized
    assert "provider_details" not in serialized
    console_output = capsys.readouterr().out
    assert "offline_fixture_p50_ms=" in console_output
    assert "timing_is_provider_speed=false" in console_output

    with pytest.raises(RuntimeError, match="overwrite"):
        _module().write_report(load_summary_for_test(), str(output))


def test_handler_report_keeps_micro_adjustment_diagnostics_without_quality_claim(
    tmp_path,
):
    from src.domain.services.meal_text_nutrition_eval_loop import (
        ParseTextEvalCaseResult,
        ParseTextEvalSummary,
    )

    result = ParseTextEvalCaseResult(
        case_id="reference-adjustment-diagnostic",
        contract_pass=True,
        identity_pass=True,
        quantity_pass=True,
        candidate_pass=True,
        reference_pass=True,
        catastrophic_outlier=False,
        source="usda",
        provider_calls=0,
        duration_ms=1,
        handler_food_count=1,
        raw_model_micro_coverage={"iron": 0.0},
        raw_model_micro_normalized_mae={"iron": 1.0},
        handler_micro_coverage={"iron": 1.0},
        handler_micro_normalized_mae={"iron": 0.0},
        evidence_kind="staging_handler_fixture_reference",
        final_output_kind="handler_final_with_reviewed_reference",
    )
    summary = ParseTextEvalSummary(
        case_count=1,
        contract_pass_rate=1,
        identity_quantity_pass_rate=1,
        candidate_pass_rate=1,
        common_reference_pass_rate=1,
        catastrophic_outliers=0,
        invalid_reference_accepts=0,
        provider_calls=(0,),
        latency_p50_ms=1,
        latency_p95_ms=1,
        cases=(result,),
        evidence_kind="staging_handler_fixture_reference",
    )
    output = tmp_path / "handler-eval.json"

    _module().write_report(summary, str(output))
    report = json.loads(output.read_text())

    assert report["quality_metrics"] is None
    assert report["reference_adjustment_diagnostics"] == [
        {
            "case_id": "reference-adjustment-diagnostic",
            "raw_model_micro_coverage": {"iron": 0.0},
            "raw_model_micro_normalized_mae": {"iron": 1.0},
            "handler_final_micro_coverage": {"iron": 1.0},
            "handler_final_micro_normalized_mae": {"iron": 0.0},
        }
    ]


def test_explicit_repository_report_path_must_be_ignored():
    output = Path.cwd() / "tests" / "fixtures" / "temporary-eval-report.json"
    with pytest.raises(RuntimeError, match="git-ignored"):
        _module()._open_report(str(output))


@pytest.mark.asyncio
async def test_live_runner_stops_at_hard_case_deadline(monkeypatch):
    module = _module()
    case = module.load_corpus()[0][0]

    async def slow_case(*_args, **_kwargs):
        await asyncio.sleep(0.05)

    monkeypatch.setattr(module, "LIVE_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(module, "_run_case", slow_case)

    with pytest.raises(RuntimeError, match="admitted no cases"):
        await module.run_live([case])


def test_live_runner_reserves_worst_case_provider_capacity():
    module = _module()
    case = module.load_corpus()[0][0]
    case.ai_payload["items"] = [{}]

    assert module._case_item_count(case) == module.MAX_TEXT_PARSE_ITEMS == 20


def test_provider_only_batch_offset_selects_later_bounded_cases():
    module = _module()
    cases, _ = module.load_corpus()
    admitted, selected = module.select_provider_batch(
        cases, repetitions=2, max_cases=25, case_offset=3
    )

    assert admitted == min(12, len(cases) - 3)
    assert selected == cases[3 : 3 + admitted]
    with pytest.raises(ValueError, match="non-negative"):
        module.select_provider_batch(cases, 1, 10, case_offset=-1)


def test_provider_only_mode_fails_closed_before_manifest_or_provider_work(monkeypatch):
    module = _module()
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("PARSE_TEXT_LIVE_EVAL_ENABLED", raising=False)

    assert module.main(["--mode", "provider-only"]) == 1


def test_prompt_bundle_accepts_validated_localized_snapshot(tmp_path):
    module = _module()
    prompt_en = "parse prompt en"
    prompt_vi = "parse prompt vi"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "prompts": {
                    "parse_text:en": {
                        "text": prompt_en,
                        "sha256": hashlib.sha256(prompt_en.encode()).hexdigest(),
                    },
                    "parse_text:vi": {
                        "text": prompt_vi,
                        "sha256": hashlib.sha256(prompt_vi.encode()).hexdigest(),
                    },
                }
            }
        )
    )

    bundle, _ = module.load_prompt_bundle(snapshot, family="parse_text")

    assert bundle == {"en": prompt_en, "vi": prompt_vi}
    assert set(module._rendered_prompt_hashes(bundle, {"en", "vi"})) == {"en", "vi"}


def test_prompt_snapshot_hash_mismatch_is_rejected(tmp_path):
    module = _module()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {"prompts": {"parse_text:en": {"text": "prompt", "sha256": "wrong"}}}
        )
    )

    with pytest.raises(ValueError, match="hash mismatch"):
        module.load_prompt_bundle(snapshot, family="parse_text")


def test_reviewed_text_manifest_validates_references_and_reports_incomplete_corpus(
    tmp_path,
):
    module = _module()
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
                        "case_id": "text-case-1",
                        "split": "development",
                        "language": "en",
                        "text": "one bowl of rice",
                        "expected_items": [
                            {"aliases": ["rice"], "quantity_g": [180, 220]}
                        ],
                        "expected_calories_kcal": [200, 300],
                        "expected_macros": {"carbs_g": [40, 65]},
                        "expected_micros": {
                            "vitamin_a": {"status": "unknown", "unit": "mcg"}
                        },
                    }
                ],
            }
        )
    )

    cases, splits = module.load_reviewed_provider_manifest(manifest)

    assert len(cases) == 1
    assert cases[0].expected_items[0]["quantity_g"] == [180, 220]
    assert splits[cases[0].case_id] == "development"
    assert (
        module._text_corpus_status(cases, splits)
        == "blocked_incomplete_reviewed_corpus"
    )


def test_balanced_text_corpus_needs_all_macro_and_micro_references():
    from types import SimpleNamespace

    from src.domain.evaluation.meal_text_nutrition_eval_models import (
        TEXT_MACROS,
        TEXT_MICRO_UNITS,
    )

    module = _module()

    def make_cases(include_references):
        cases = []
        splits = {}
        for split, rows in (("development", 20), ("held_out", 10)):
            for index in range(rows):
                case_id = f"meal-case-{split}-{index}"
                case = SimpleNamespace(
                    case_id=case_id,
                    language="en" if index % 2 == 0 else "vi",
                    expected_meal_macros=(
                        dict.fromkeys(TEXT_MACROS, (1.0, 2.0))
                        if include_references
                        else {}
                    ),
                    expected_meal_micros=(
                        {
                            name: {"status": "known", "unit": unit, "value": 1.0}
                            for name, unit in TEXT_MICRO_UNITS.items()
                        }
                        if include_references
                        else {}
                    ),
                )
                cases.append(case)
                splits[case_id] = split
        return cases, splits

    cases, splits = make_cases(True)
    assert module._text_corpus_status(cases, splits) == "reviewed_corpus_complete"
    cases, splits = make_cases(False)
    assert (
        module._text_corpus_status(cases, splits)
        == "blocked_incomplete_nutrition_references"
    )


def load_summary_for_test():
    from src.domain.services.meal_text_nutrition_eval_loop import ParseTextEvalSummary

    return ParseTextEvalSummary(
        case_count=1,
        contract_pass_rate=1,
        identity_quantity_pass_rate=1,
        candidate_pass_rate=1,
        common_reference_pass_rate=1,
        catastrophic_outliers=0,
        invalid_reference_accepts=0,
        provider_calls=(0,),
        latency_p50_ms=1,
        latency_p95_ms=1,
        cases=(),
    )
