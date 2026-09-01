import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_script_path = REPO_ROOT / "scripts" / "development" / "evaluate_meal_scan_quality.py"
_spec = importlib.util.spec_from_file_location(
    "evaluate_meal_scan_quality", _script_path
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)

load_barcode_corpus = _mod.load_barcode_corpus
load_food_label_corpus = _mod.load_food_label_corpus
load_meal_image_corpus = _mod.load_meal_image_corpus
main = _mod.main


def test_corpus_loaders_load_valid_fixtures():
    meal_cases = load_meal_image_corpus()
    assert len(meal_cases) >= 60

    label_cases = load_food_label_corpus()
    assert len(label_cases) >= 60

    barcode_cases = load_barcode_corpus()
    assert len(barcode_cases) >= 100


@pytest.mark.asyncio
async def test_cli_runner_offline_all_passes(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_meal_scan_quality.py",
            "--surface",
            "all",
            "--mode",
            "offline",
            "--max-cases",
            "5",
        ],
    )
    exit_code = await main()
    assert exit_code == 0


def test_corpus_cases_expose_description_for_live_runners():
    meal_case = load_meal_image_corpus()[0]
    label_case = load_food_label_corpus()[0]
    assert meal_case.description or meal_case.expected_dish_name
    assert label_case.description or label_case.expected_product_name


def test_live_barcode_adapters_are_importable():
    from src.infra.adapters.fat_secret_service import FatSecretService
    from src.infra.adapters.open_food_facts_service import OpenFoodFactsService

    assert FatSecretService is not None
    assert OpenFoodFactsService is not None


def test_canonical_visible_estimate_fails_quarantine_observation():
    observation = _mod._observe_barcode_quarantine(
        is_estimate=True,
        source="ai_estimate",
        provider_responses={"canonical_visible": True},
    )
    assert observation is False


@pytest.mark.asyncio
async def test_cli_live_mode_requires_surface_flags(monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.delenv("BARCODE_LIVE_EVAL_ENABLED", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluate_meal_scan_quality.py",
            "--surface",
            "barcode",
            "--mode",
            "live",
            "--confirm-live-staging",
        ],
    )
    exit_code = await main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "BARCODE_LIVE_EVAL_ENABLED=true" in captured.err
