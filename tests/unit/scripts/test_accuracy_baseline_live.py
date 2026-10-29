"""Live accuracy scoring stays offline-safe and measures grounding key misses."""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]


def _modules():
    """Import the harness after pointing ``scripts`` at the repository package."""
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    package = sys.modules.get("scripts")
    if package is not None:
        package.__path__ = [str(_ROOT / "scripts")]
    from scripts.development import accuracy_baseline_live as live
    from scripts.development import accuracy_baseline_metrics as metrics

    return live, metrics


def _case(
    case_id: str,
    *,
    status: str = "labeled",
    kcal=(100, 100),
    protein=(10, 10),
    carbs=(20, 20),
    fat=(5, 5),
) -> dict:
    macros = {}
    if protein is not None:
        macros["protein_g"] = list(protein)
    if carbs is not None:
        macros["carbs_g"] = list(carbs)
    if fat is not None:
        macros["fat_g"] = list(fat)
    return {
        "case_id": case_id,
        "expected_calories_kcal": None if kcal is None else list(kcal),
        "expected_macros": macros,
        "label_status": status,
        "split": "development",
    }


def test_interval_mae_mape_and_tolerance_bands():
    _live, metrics = _modules()
    score_case = metrics.score_case
    aggregate_cases = metrics.aggregate_cases
    scored = score_case(
        _case("eval-scan-001"),
        predicted={"kcal": 120, "protein_g": 10, "carbs_g": 20, "fat_g": 5},
        error_type=None,
        duration_ms=100,
    )
    inside = score_case(
        _case("eval-scan-002", kcal=(90, 110)),
        predicted={"kcal": 100, "protein_g": 10, "carbs_g": 20, "fat_g": 5},
        error_type=None,
        duration_ms=50,
    )
    zero = score_case(
        _case("eval-scan-003", kcal=(0, 0), protein=(0, 0), carbs=(0, 0), fat=(0, 0)),
        predicted={"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0},
        error_type=None,
        duration_ms=10,
    )
    summary = aggregate_cases([scored, inside, zero])
    kcal = summary["nutrients"]["kcal"]

    assert scored["nutrients"]["kcal"]["absolute_error"] == 20
    assert scored["nutrients"]["kcal"]["within_10"] is False
    assert scored["nutrients"]["kcal"]["within_20"] is True
    assert inside["nutrients"]["kcal"]["absolute_error"] == 0
    assert zero["nutrients"]["kcal"]["percentage_error"] is None
    assert zero["nutrients"]["kcal"]["within_20"] is True
    assert kcal["scored_cases"] == 3
    assert kcal["mae"] == pytest.approx(20 / 3)
    assert kcal["mape_cases"] == 2
    assert kcal["mape"] == pytest.approx(0.1)
    assert kcal["within_20_rate"] == 1
    assert summary["latency_ms"]["p50_ms"] == 50


def test_unscored_partial_and_not_food_stay_out_of_mae():
    _live, metrics = _modules()
    score_case = metrics.score_case
    aggregate_cases = metrics.aggregate_cases
    unscored = score_case(
        _case(
            "eval-scan-196",
            status="unscored",
            kcal=None,
            protein=None,
            carbs=None,
            fat=None,
        ),
        predicted={"kcal": 400, "protein_g": 1, "carbs_g": 1, "fat_g": 1},
        error_type=None,
        duration_ms=1,
    )
    partial = score_case(
        _case("eval-scan-113", status="partial", fat=None),
        predicted={"kcal": 100, "protein_g": 10, "carbs_g": 20, "fat_g": 99},
        error_type=None,
        duration_ms=1,
    )
    rejected = score_case(
        _case("eval-scan-144"),
        predicted=None,
        error_type="NotFood",
        duration_ms=1,
    )
    summary = aggregate_cases([unscored, partial, rejected])

    assert unscored["outcome"] == "unscored_label"
    assert partial["outcome"] == "partial"
    assert partial["nutrients"]["fat_g"]["scored"] is False
    assert partial["nutrients"]["protein_g"]["scored"] is True
    assert rejected["outcome"] == "not_food"
    assert summary["nutrients"]["kcal"]["scored_cases"] == 1
    assert summary["nutrients"]["kcal"]["mae"] == 0
    assert summary["nutrients"]["fat_g"]["scored_cases"] == 0
    assert summary["unscored_label_cases"] == 1
    assert summary["not_food_cases"] == 1


def test_lookup_key_mismatch_is_counted_separately_from_a_catalog_hit():
    live, metrics = _modules()
    relation = live.key_relation("grilled chicken")

    assert relation.keys_differ is True
    assert relation.lookup_key == "grilled chicken"
    assert relation.catalog_key == "chicken"
    summary = metrics.summarize_grounding_items(
        [
            {
                "catalog_key_would_hit": True,
                "keys_differ": True,
                "product_local_hit": False,
                "tier": "ai_estimate",
            },
            {
                "catalog_key_would_hit": False,
                "keys_differ": False,
                "product_local_hit": True,
                "tier": "local",
            },
            {
                "catalog_key_would_hit": None,
                "keys_differ": True,
                "product_local_hit": False,
                "tier": "fatsecret",
            },
        ]
    )

    assert summary["items"] == 3
    assert summary["local_hits"] == 1
    assert summary["local_misses"] == 2
    assert summary["key_mismatch_items"] == 2
    assert summary["key_mismatch_and_local_miss"] == 2
    assert summary["catalog_key_diagnostic"] == "unavailable"
    assert summary["key_mismatch_catalog_key_would_hit"] is None
    assert summary["tiers"] == {"ai_estimate": 1, "fatsecret": 1, "local": 1}


def test_catalog_diagnostic_counts_a_key_the_product_lookup_missed():
    _live, metrics = _modules()
    summary = metrics.summarize_grounding_items(
        [
            {
                "catalog_key_would_hit": True,
                "keys_differ": True,
                "product_local_hit": False,
                "tier": "ai_estimate",
            }
        ]
    )

    assert summary["catalog_key_diagnostic"] == "queried"
    assert summary["key_mismatch_catalog_key_would_hit"] == 1


@pytest.mark.asyncio
async def test_controlled_live_scores_both_arms_from_one_vision_pass():
    live, _metrics = _modules()
    scan_cases = [_case("eval-scan-001"), _case("eval-scan-002", kcal=(90, 110))]
    text_cases = [_case("eval-prompt-003", kcal=(150, 300))]

    async def scan_invoke(case):
        return live.ScanCapture(
            case_id=case["case_id"],
            duration_ms=80,
            predicted={"kcal": 100, "protein_g": 10, "carbs_g": 20, "fat_g": 5},
            error_type=None,
            usage=[{"input_tokens": 10, "model": "vision-test", "output_tokens": 4}],
            item_keys=(live.key_relation("grilled chicken"),),
        )

    async def ground_invoke(_capture: live.ScanCapture):
        return live.GroundCapture(
            duration_ms=20,
            predicted={"kcal": 110, "protein_g": 10, "carbs_g": 20, "fat_g": 5},
            error_type=None,
            outcome="match",
            items=[
                {
                    "catalog_key_would_hit": True,
                    "keys_differ": True,
                    "product_local_hit": False,
                    "tier": "ai_estimate",
                }
            ],
        )

    async def text_invoke(case):
        return live.ScanCapture(
            case_id=case["case_id"],
            duration_ms=30,
            predicted={"kcal": 200, "protein_g": 10, "carbs_g": 20, "fat_g": 5},
            error_type=None,
            usage=[
                {"input_tokens": 6, "model": "text-test", "output_tokens": 2},
                {
                    "fatsecret_details": 1,
                    "fatsecret_searches": 1,
                    "input_tokens": 0,
                    "model": "",
                    "output_tokens": 0,
                    "purpose": "parse_text_reference_count",
                },
            ],
            exceeded_product_deadline=False,
        )

    report = await live.run_controlled_live(
        scan_cases,
        text_cases,
        scan_invoke=scan_invoke,
        text_invoke=text_invoke,
        ground_invoke=ground_invoke,
        arms="both",
        concurrency=2,
        metadata={"git_sha": "abc123"},
    )

    assert report["provider_calls"] == 5
    assert report["scan"]["grounding_off"]["nutrients"]["kcal"]["mae"] == 0
    assert report["scan"]["grounding_on"]["nutrients"]["kcal"]["mae"] == pytest.approx(
        5
    )
    assert report["scan"]["grounding_on"]["latency_ms"]["mean_ms"] == 100
    assert report["text"]["nutrients"]["kcal"]["mae"] == 0
    assert report["text"]["cases"][0]["exceeded_product_deadline"] is False
    assert (
        report["scan"]["grounding_diagnostics"]["key_mismatch_catalog_key_would_hit"]
        == 2
    )
    assert report["cost"]["shared_model"]["vision"]["input_tokens"] == 20
    assert report["cost"]["text_reference_calls"] == 2
    assert report["cost"]["arm_off"]["usd"] is None
    assert report["grounding_arm"]["process_env_changed"] is False
    assert "https://" not in live.render_live_summary(report)
    assert "mae=" in live.render_live_summary(report)


def test_live_mode_requires_confirmation_and_a_key(tmp_path, capsys, monkeypatch):
    live, _metrics = _modules()
    from scripts.development import run_accuracy_baseline as module

    monkeypatch.setattr(live, "_openai_key", lambda: "")
    output = tmp_path / "live.json"

    assert module.main(["--mode", "live", "--output", str(output)]) == 1
    assert "confirm-live" in capsys.readouterr().err
    assert (
        module.main(["--mode", "live", "--confirm-live", "--output", str(output)]) == 1
    )
    captured = capsys.readouterr()
    assert "OPENAI_API_KEY" in captured.err
    assert "No provider call was made" in captured.err
    assert not output.exists()
