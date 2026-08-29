import importlib.util
from pathlib import Path

from src.app.services.catalog_meal_seed_import_service import (
    CatalogSeedImportSummary,
    CatalogSeedReviewRequired,
)
from src.domain.services.meal_recommendation.catalog_recipe_seed_validator import (
    CatalogSeedValidationResult,
)

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "import_catalog_recipe_seeds.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "import_catalog_recipe_seeds", _SCRIPT_PATH
)
assert _SPEC is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
_build_import_report = _MODULE._build_import_report


def test_build_import_report_includes_validation_and_review_evidence():
    report = _build_import_report(
        validation=CatalogSeedValidationResult(
            manifest_digest="a" * 64,
            recipe_count=180,
            coverage={
                "vietnamese": {"lunch": 60, "breakfast": 5},
                "japanese": {"dinner": 20},
            },
        ),
        summary=CatalogSeedImportSummary(
            dry_run=True,
            review_required=(
                CatalogSeedReviewRequired(
                    recipe_index=7,
                    recipe_key="vietnamese-com-tam-007",
                    reason="near_duplicate",
                    matched_catalog_key="vietnamese-com-tam-001",
                    ingredient_jaccard=0.875,
                ),
            ),
        ),
    )

    assert report["manifest_digest"] == "a" * 64
    assert report["recipe_count"] == 180
    assert report["coverage"]["vietnamese"] == {"breakfast": 5, "lunch": 60}
    assert report["review_required"] == [
        {
            "recipe_index": 7,
            "recipe_key": "vietnamese-com-tam-007",
            "reason": "near_duplicate",
            "matched_catalog_key": "vietnamese-com-tam-001",
            "ingredient_jaccard": 0.875,
        }
    ]
    assert report["validation_errors"] == []
