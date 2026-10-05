from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.base_dependencies import get_async_db
from src.api.dependencies.auth import require_admin_or_local
from src.api.routes.v1 import admin_meal_catalog_import as route_mod
from src.app.services.catalog_food_reference_review_service import (
    CatalogFoodReferenceApproval,
)
from src.app.services.catalog_meal_seed_import_service import (
    CatalogSeedCandidateEnrichmentSummary,
    CatalogSeedImportSummary,
    CatalogSeedUnverifiedReference,
)


def test_resolve_catalog_manifest_is_dry_run_and_does_not_commit(monkeypatch):
    db = AsyncMock()
    calls = []

    async def fake_run_importer(db_arg, request, *, dry_run):
        calls.append(dry_run)
        return CatalogSeedImportSummary(dry_run=dry_run, inserted=1)

    monkeypatch.setattr(route_mod, "_run_importer", fake_run_importer)
    client = _client(db)

    response = client.post("/v1/admin/meal-catalog/resolve", json=_request())

    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert response.json()["dry_run"] is True
    assert calls == [True]
    db.commit.assert_not_awaited()


def test_approve_food_reference_commits_admin_review_decision():
    db = AsyncMock()

    class Reviewer:
        async def approve(self, food_reference_id):
            assert food_reference_id == 42
            return CatalogFoodReferenceApproval(
                food_reference_id=42,
                name="Rice noodles, cooked",
                source="fatsecret",
                is_verified=True,
            )

    response = _client(db, reviewer=Reviewer()).post(
        "/v1/admin/meal-catalog/approve-food-reference",
        json={"food_reference_id": 42},
    )

    assert response.status_code == 200
    assert response.json()["is_verified"] is True
    db.commit.assert_awaited_once()


def test_approve_missing_food_reference_does_not_commit():
    db = AsyncMock()

    class Reviewer:
        async def approve(self, food_reference_id):
            return None

    response = _client(db, reviewer=Reviewer()).post(
        "/v1/admin/meal-catalog/approve-food-reference",
        json={"food_reference_id": 404},
    )

    assert response.status_code == 404
    db.commit.assert_not_awaited()


def test_import_catalog_manifest_previews_then_commits(monkeypatch):
    db = AsyncMock()
    calls = []

    async def fake_run_importer(db_arg, request, *, dry_run):
        calls.append(dry_run)
        return CatalogSeedImportSummary(dry_run=dry_run, inserted=1)

    monkeypatch.setattr(route_mod, "_run_importer", fake_run_importer)
    preparer = AsyncMock(return_value=3)
    client = _client(db, preparer=preparer)

    response = client.post("/v1/admin/meal-catalog/import", json=_request())

    assert response.status_code == 200
    assert response.json()["applied"] is True
    assert response.json()["inserted"] == 1
    assert calls == [True, False]
    db.commit.assert_awaited_once()
    preparer.assert_awaited_once()


def test_import_preparation_failure_still_reports_applied_import(monkeypatch):
    db = AsyncMock()

    async def fake_run_importer(db_arg, request, *, dry_run):
        return CatalogSeedImportSummary(dry_run=dry_run, inserted=1)

    monkeypatch.setattr(route_mod, "_run_importer", fake_run_importer)
    preparer = AsyncMock(side_effect=RuntimeError("provider down"))

    response = _client(db, preparer=preparer).post(
        "/v1/admin/meal-catalog/import", json=_request()
    )

    assert response.status_code == 200
    assert response.json()["applied"] is True
    db.commit.assert_awaited_once()


def test_dry_run_import_does_not_prepare_catalog(monkeypatch):
    db = AsyncMock()

    async def fake_run_importer(db_arg, request, *, dry_run):
        return CatalogSeedImportSummary(dry_run=dry_run, inserted=1)

    monkeypatch.setattr(route_mod, "_run_importer", fake_run_importer)
    preparer = AsyncMock(return_value=0)
    payload = _request()
    payload["dry_run"] = True

    response = _client(db, preparer=preparer).post(
        "/v1/admin/meal-catalog/import", json=payload
    )

    assert response.status_code == 200
    assert response.json()["applied"] is False
    preparer.assert_not_awaited()


def test_resolve_returns_structured_unverified_references(monkeypatch):
    db = AsyncMock()

    async def fake_run_importer(db_arg, request, *, dry_run):
        return CatalogSeedImportSummary(
            dry_run=dry_run,
            errors=("food_reference_not_verified",),
            unverified_references=(
                CatalogSeedUnverifiedReference(
                    recipe_index=0,
                    recipe_key="pho-ga-001",
                    ingredient_index=0,
                    ingredient_name="Chicken breast",
                    food_reference_id=1,
                    food_reference_name="Chicken breast",
                    source="seed",
                ),
            ),
        )

    monkeypatch.setattr(route_mod, "_run_importer", fake_run_importer)
    response = _client(db).post("/v1/admin/meal-catalog/resolve", json=_request())

    assert response.status_code == 200
    assert response.json()["unverified_references"][0]["food_reference_id"] == 1


def test_import_catalog_manifest_returns_validation_report_without_db_work():
    db = AsyncMock()
    client = _client(db)
    payload = _request()
    payload["manifest"]["recipes"][0]["meal_types"] = []

    response = client.post("/v1/admin/meal-catalog/import", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["validation"]["errors"]
    db.commit.assert_not_awaited()


def test_enrich_catalog_candidates_commits_without_importing_recipes():
    db = AsyncMock()

    class Importer:
        async def enrich_missing_candidates(self, manifest):
            return CatalogSeedCandidateEnrichmentSummary(
                attempted=4,
                enriched=3,
                skipped_existing=1,
            )

    client = _client(db, importer=Importer())

    response = client.post("/v1/admin/meal-catalog/enrich", json=_request())

    assert response.status_code == 200
    assert response.json()["enriched"] == 3
    assert response.json()["skipped_existing"] == 1
    db.commit.assert_awaited_once()


def test_enrich_catalog_candidates_rejects_invalid_manifest_without_commit():
    db = AsyncMock()
    payload = _request()
    payload["manifest"]["recipes"][0]["meal_types"] = []
    client = _client(db)

    response = client.post("/v1/admin/meal-catalog/enrich", json=payload)

    assert response.status_code == 422
    db.commit.assert_not_awaited()


def _client(db, importer=object, reviewer=object, preparer=None):
    app = FastAPI()
    app.include_router(route_mod.router)
    app.dependency_overrides[get_async_db] = lambda: db
    app.dependency_overrides[route_mod.get_catalog_meal_seed_importer] = lambda: (
        importer
    )
    app.dependency_overrides[route_mod.get_catalog_preparer] = lambda: (
        preparer or AsyncMock(return_value=0)
    )
    app.dependency_overrides[route_mod.get_catalog_food_reference_review_service] = (
        lambda: reviewer
    )
    app.dependency_overrides[require_admin_or_local] = lambda: "admin@nutree.ai"
    return TestClient(app)


def _request():
    return {
        "manifest": {
            "release_key": "test-release",
            "expected_recipe_count": 1,
            "recipes": [
                {
                    "recipe_key": "pho-ga-001",
                    "cuisine": "vietnamese",
                    "name": "Pho Ga",
                    "meal_types": ["lunch"],
                    "ingredients": [
                        {
                            "food_reference_id": 1,
                            "name": "Chicken breast",
                            "quantity": 120,
                            "unit": "g",
                        }
                    ],
                }
            ],
        },
        "partial": True,
        "min_per_cuisine_meal_type": 0,
    }
