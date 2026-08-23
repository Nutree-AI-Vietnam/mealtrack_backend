import asyncio
import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


def _module():
    path = Path("scripts/development/evaluate_parse_text_nutrition.py").resolve()
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


def test_offline_cli_writes_private_non_raw_report_without_overwrite(tmp_path):
    output = tmp_path / "eval.json"
    assert _module().main(["--mode", "offline", "--output", str(output)]) == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    report = json.loads(output.read_text())
    serialized = output.read_text()
    assert report["case_count"] >= 10
    assert "100gr khoai tay" not in serialized
    assert "provider_details" not in serialized

    with pytest.raises(RuntimeError, match="overwrite"):
        _module().write_report(load_summary_for_test(), str(output))


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
    case.ai_payload["items"] = [{}] * 20

    assert module._case_item_count(case) == module.MAX_HANDLER_ITEMS


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
