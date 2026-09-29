from types import SimpleNamespace

import pytest

from src.app.services.catalog_meal_seed_import_service import (
    CatalogMealSeedImporter,
    CatalogSeedResolutionCandidate,
    _content_hash,
)
from src.domain.ports.food_reference_repository_port import (
    FoodReferenceNutritionProjection,
)
from src.observability import (
    reset_observability_connector_for_test,
    set_observability_connector_for_test,
)


class _Metrics:
    def __init__(self):
        self.calls = []

    def initialize(self):
        return None

    def capture_exception(self, error, *, context=None):
        return None

    def capture_message(self, message, *, level="info", context=None):
        return None

    def log_event(self, level, message, *, attributes=None):
        return None

    def increment_metric(self, name, value=1.0, *, unit=None, attributes=None):
        self.calls.append(("increment", name, value, unit, attributes))

    def gauge_metric(self, name, value, *, unit=None, attributes=None):
        self.calls.append(("gauge", name, value, unit, attributes))

    def distribution_metric(self, name, value, *, unit=None, attributes=None):
        self.calls.append(("distribution", name, value, unit, attributes))

    def set_request_context(self, *, request_id, method, path, user_id=None):
        return None

    def start_span(self, *, operation, description=None, context=None):
        from contextlib import nullcontext

        return nullcontext()

    def flush(self, *, timeout=5):
        return None


def teardown_function():
    reset_observability_connector_for_test()


class _Session:
    def __init__(self):
        self.added = []
        self.updated = []
        self.flushed = False
        self.locked = False
        self.signatures = []

    async def add_seed_meal(self, row):
        self.added.append(row)
        self.flushed = True

    async def update_popularity_rank(self, *, catalog_key, popularity_rank):
        self.updated.append((catalog_key, popularity_rank))

    async def find_seed_existing(self, *, catalog_key, content_hash):
        return None

    async def lock_seed_import(self):
        self.locked = True

    async def list_seed_signatures(self):
        return list(self.signatures)


class _FoodReferenceRepository:
    async def get_nutrition_projection(self, food_reference_id):
        return None

    async def list_catalog_seed_candidates(self):
        return []

    async def find_catalog_seed_candidates_by_normalized_name(self, name_normalized):
        return []


class _Importer(CatalogMealSeedImporter):
    def __init__(
        self,
        *,
        refs_by_id=None,
        refs_by_name=None,
        existing=None,
        candidates=None,
        approved_mappings=None,
        auto_resolve_threshold=0.92,
        resolve_all_best_effort=False,
        candidate_enricher=None,
        allow_unmapped_ingredients=False,
        overwrite_existing=False,
        skip_existing=False,
    ):
        self.session = _Session()
        super().__init__(
            self.session,
            _FoodReferenceRepository(),
            approved_mappings=approved_mappings,
            auto_resolve_threshold=auto_resolve_threshold,
            resolve_all_best_effort=resolve_all_best_effort,
            candidate_enricher=candidate_enricher,
            allow_unmapped_ingredients=allow_unmapped_ingredients,
            overwrite_existing=overwrite_existing,
            skip_existing=skip_existing,
        )
        self.refs_by_id = refs_by_id or {}
        self.refs_by_name = refs_by_name or {}
        self.existing = existing
        self.candidates = candidates or []
        self.session.signatures = []

    async def _get_reference_by_id(self, food_reference_id):
        if food_reference_id in self.refs_by_id:
            return self.refs_by_id[food_reference_id]
        for refs in self.refs_by_name.values():
            for ref in refs:
                if ref.id == food_reference_id:
                    return ref
        return None

    async def _find_reference_candidates_by_normalized_name(self, name_normalized):
        return [
            CatalogSeedResolutionCandidate(
                food_reference_id=ref.id,
                name=ref.name,
                name_normalized=name_normalized,
                source=ref.source,
                is_verified=ref.is_verified,
                score=1.0,
            )
            for ref in self.refs_by_name.get(name_normalized, [])
        ]

    async def _find_existing(self, catalog_key, content_hash):
        if self.existing == "exact":
            return SimpleNamespace(catalog_key=catalog_key, content_hash=content_hash)
        if self.existing == "changed":
            return SimpleNamespace(catalog_key=catalog_key, content_hash="different")
        if self.existing == "same-content-different-key":
            return SimpleNamespace(catalog_key="other-key", content_hash=content_hash)
        return None

    async def _ranked_candidates(self, normalized_name):
        return self.candidates


def _reference(food_reference_id=7, *, name="Rice", is_verified=True):
    return FoodReferenceNutritionProjection(
        id=food_reference_id,
        name=name,
        source="catalog_seed",
        is_verified=is_verified,
        protein_100g=2.7,
        carbs_100g=28.0,
        fat_100g=0.3,
        fiber_100g=0.4,
        sugar_100g=0.1,
        density_g_ml=1.0,
    )


def _manifest(*, food_reference_id=7):
    return {
        "recipes": [
            {
                "recipe_key": "vn-rice-breakfast",
                "cuisine": "vietnamese",
                "name": "Rice Breakfast",
                "meal_types": ["breakfast"],
                "ingredients": [
                    {
                        "food_reference_id": food_reference_id,
                        "name": "Rice",
                        "quantity": 100,
                        "unit": "g",
                    }
                ],
            }
        ]
    }


def _resolved_ingredient(*, food_reference_id=7, quantity=100.0, unit="g"):
    return SimpleNamespace(
        food_reference_id=food_reference_id,
        display_name="Rice",
        quantity=quantity,
        unit=unit,
    )


def _egg_manifest():
    manifest = _manifest(food_reference_id=None)
    manifest["recipes"][0]["ingredients"][0]["name"] = "Egg"
    manifest["recipes"][0]["ingredients"][0]["quantity"] = 1
    manifest["recipes"][0]["ingredients"][0]["unit"] = "each"
    return manifest


@pytest.mark.asyncio
async def test_import_inserts_catalog_meal_without_derived_nutrition_snapshots():
    importer = _Importer(refs_by_id={7: _reference()})

    summary = await importer.import_manifest(_manifest())

    assert summary.inserted == 1
    assert summary.skipped_existing == 0
    assert summary.errors == ()
    assert importer.session.flushed is True
    row = importer.session.added[0]
    assert row.catalog_key == "vn-rice-breakfast"
    assert row.meal_types == ("breakfast",)
    assert not hasattr(row, "protein_g")
    assert not hasattr(row, "servings")
    assert row.ingredients[0].food_reference_id == 7
    assert row.ingredients[0].display_name == "Rice"
    assert not hasattr(row.ingredients[0], "resolved_grams")


@pytest.mark.asyncio
async def test_import_resolves_null_food_reference_id_by_normalized_name():
    importer = _Importer(refs_by_name={"rice": [_reference()]})

    summary = await importer.import_manifest(_manifest(food_reference_id=None))

    assert summary.inserted == 1
    assert summary.errors == ()
    assert importer.session.added[0].ingredients[0].food_reference_id == 7


@pytest.mark.asyncio
async def test_import_skips_exact_existing_catalog_meal():
    importer = _Importer(refs_by_id={7: _reference()}, existing="exact")

    summary = await importer.import_manifest(_manifest())

    assert summary.inserted == 0
    assert summary.skipped_existing == 1
    assert importer.session.added == []


@pytest.mark.asyncio
async def test_import_updates_popularity_rank_for_exact_existing_catalog_meal():
    importer = _Importer(refs_by_id={7: _reference()}, existing="exact")
    manifest = _manifest()
    manifest["recipes"][0]["popularity_rank"] = 4

    summary = await importer.import_manifest(manifest)

    assert summary.inserted == 0
    assert summary.updated == 1
    assert summary.skipped_existing == 1
    assert importer.session.updated == [("vn-rice-breakfast", 4)]


@pytest.mark.asyncio
async def test_import_does_not_update_rank_for_same_content_under_another_key():
    importer = _Importer(
        refs_by_id={7: _reference()}, existing="same-content-different-key"
    )
    manifest = _manifest()
    manifest["recipes"][0]["popularity_rank"] = 4

    summary = await importer.import_manifest(manifest)

    assert summary.updated == 0
    assert importer.session.updated == []


@pytest.mark.asyncio
async def test_import_rejects_existing_catalog_key_with_changed_content():
    importer = _Importer(refs_by_id={7: _reference()}, existing="changed")

    summary = await importer.import_manifest(_manifest())

    assert summary.inserted == 0
    assert "catalog_key already exists with different content" in summary.errors[0]


@pytest.mark.asyncio
async def test_import_with_overwrite_existing_updates_changed_content():
    importer = _Importer(
        refs_by_id={7: _reference()},
        existing="changed",
        overwrite_existing=True,
    )

    summary = await importer.import_manifest(_manifest())

    assert summary.is_successful is True
    assert summary.inserted == 1
    assert len(importer.session.added) == 1
    assert importer.session.added[0].catalog_key == "vn-rice-breakfast"


@pytest.mark.asyncio
async def test_import_with_skip_existing_skips_changed_content():
    importer = _Importer(
        refs_by_id={7: _reference()},
        existing="changed",
        skip_existing=True,
    )

    summary = await importer.import_manifest(_manifest())

    assert summary.is_successful is True
    assert summary.inserted == 0
    assert summary.skipped_existing == 1
    assert importer.session.added == []


@pytest.mark.asyncio
async def test_import_reports_unresolved_food_reference_candidates():
    importer = _Importer(
        candidates=[
            CatalogSeedResolutionCandidate(
                food_reference_id=1,
                name="White rice",
                name_normalized="white rice",
                source="catalog_seed",
                is_verified=True,
                score=0.7,
            ),
            CatalogSeedResolutionCandidate(
                food_reference_id=2,
                name="Brown rice",
                name_normalized="brown rice",
                source="catalog_seed",
                is_verified=True,
                score=0.6,
            ),
        ],
        auto_resolve_threshold=None,
    )

    summary = await importer.import_manifest(_manifest(food_reference_id=None))

    assert summary.inserted == 0
    assert "needs_review" in summary.errors[0]
    assert summary.resolution_issues[0].candidates[0].food_reference_id == 1
    assert (
        summary.resolution_report()["issues"][0]["candidates"][0]["name"]
        == "White rice"
    )


@pytest.mark.asyncio
async def test_import_uses_approved_mapping_for_null_food_reference_id():
    importer = _Importer(
        refs_by_id={8: _reference(8, name="White rice")},
        approved_mappings={"Rice": 8},
    )

    summary = await importer.import_manifest(_manifest(food_reference_id=None))

    assert summary.inserted == 1
    assert summary.errors == ()
    assert importer.session.added[0].ingredients[0].food_reference_id == 8


@pytest.mark.asyncio
async def test_import_auto_resolves_high_confidence_verified_candidate():
    importer = _Importer(
        refs_by_id={8: _reference(8, name="White rice")},
        candidates=[
            CatalogSeedResolutionCandidate(
                food_reference_id=8,
                name="White rice",
                name_normalized="white rice",
                source="catalog_seed",
                is_verified=True,
                score=0.96,
            )
        ],
    )

    summary = await importer.import_manifest(_manifest(food_reference_id=None))

    assert summary.inserted == 1
    assert summary.errors == ()
    assert importer.session.added[0].ingredients[0].food_reference_id == 8


@pytest.mark.asyncio
async def test_import_reports_unverified_exact_match():
    importer = _Importer(refs_by_name={"rice": [_reference(is_verified=False)]})

    summary = await importer.import_manifest(_manifest(food_reference_id=None))

    assert summary.inserted == 0
    assert "exact_match_not_verified" in summary.errors[0]
    assert summary.resolution_issues[0].candidates[0].is_verified is False


@pytest.mark.asyncio
async def test_import_reports_every_unverified_exact_match_in_one_recipe():
    manifest = _manifest(food_reference_id=None)
    manifest["recipes"][0]["ingredients"].append(
        {
            "food_reference_id": None,
            "name": "Egg",
            "quantity": 1,
            "unit": "each",
        }
    )
    importer = _Importer(
        refs_by_name={
            "rice": [_reference(7, is_verified=False)],
            "egg": [_reference(8, name="Egg", is_verified=False)],
        }
    )

    summary = await importer.import_manifest(manifest)

    assert summary.inserted == 0
    assert [issue.normalized_name for issue in summary.resolution_issues] == [
        "rice",
        "egg",
    ]
    assert len(summary.errors) == 2


@pytest.mark.asyncio
async def test_import_reports_pinned_unverified_reference_for_manifest_recovery():
    importer = _Importer(refs_by_id={7: _reference(is_verified=False)})

    summary = await importer.import_manifest(_manifest())

    assert summary.inserted == 0
    assert "food_reference_not_verified" in summary.errors[0]
    assert summary.unverified_references[0].food_reference_id == 7
    assert (
        summary.resolution_report()["unverified_references"][0]["source"]
        == "catalog_seed"
    )


@pytest.mark.asyncio
async def test_enrichment_caches_each_missing_name_once_without_importing_recipe():
    calls = []

    async def enrich(name):
        calls.append(name)
        return True

    manifest = _manifest(food_reference_id=None)
    duplicate = _manifest(food_reference_id=None)["recipes"][0]
    duplicate["recipe_key"] = "vn-rice-lunch"
    manifest["recipes"].append(duplicate)
    importer = _Importer(
        candidate_enricher=enrich,
    )

    summary = await importer.enrich_missing_candidates(manifest)

    assert calls == ["Rice"]
    assert summary.attempted == 1
    assert summary.enriched == 1
    assert importer.session.added == []


@pytest.mark.asyncio
async def test_import_does_not_enrich_missing_candidates():
    calls = []

    async def enrich(name):
        calls.append(name)
        return True

    importer = _Importer(candidate_enricher=enrich)

    summary = await importer.import_manifest(_manifest(food_reference_id=None))

    assert summary.inserted == 0
    assert "needs_review" in summary.errors[0]
    assert calls == []


@pytest.mark.asyncio
async def test_import_best_effort_accepts_unverified_exact_match():
    importer = _Importer(
        refs_by_name={"rice": [_reference(is_verified=False)]},
        auto_resolve_threshold=0.0,
        resolve_all_best_effort=True,
    )

    summary = await importer.import_manifest(_manifest(food_reference_id=None))

    assert summary.inserted == 1
    assert summary.errors == ()


@pytest.mark.asyncio
async def test_import_best_effort_accepts_top_unverified_fuzzy_candidate():
    importer = _Importer(
        refs_by_id={8: _reference(8, name="White rice", is_verified=False)},
        candidates=[
            CatalogSeedResolutionCandidate(
                food_reference_id=8,
                name="White rice",
                name_normalized="white rice",
                source="fatsecret",
                is_verified=False,
                score=0.2,
            )
        ],
        auto_resolve_threshold=0.0,
        resolve_all_best_effort=True,
    )

    summary = await importer.import_manifest(_manifest(food_reference_id=None))

    assert summary.inserted == 1
    assert summary.errors == ()
    assert importer.session.added[0].ingredients[0].food_reference_id == 8


@pytest.mark.asyncio
async def test_import_best_effort_resolves_common_each_unit_for_egg():
    importer = _Importer(
        refs_by_name={"egg": [_reference(9, name="Egg", is_verified=False)]},
        auto_resolve_threshold=0.0,
        resolve_all_best_effort=True,
    )

    summary = await importer.import_manifest(_egg_manifest())

    assert summary.inserted == 1
    assert summary.errors == ()
    assert importer.session.added[0].ingredients[0].unit == "each"


def test_content_hash_is_invariant_to_unicode_case_whitespace_and_decimal_format():
    composed = {
        "name": "  PHỞ  ",
        "cuisine": " Vietnamese ",
        "description": "Display copy",
        "image_url": "https://example.com/a.jpg",
        "meal_types": ["lunch", "breakfast"],
    }
    decomposed = {
        "name": "pho\u031b\u0309",
        "cuisine": "vietnamese",
        "description": "Different display copy",
        "image_url": "https://example.com/b.jpg",
        "meal_types": ["breakfast", "lunch"],
    }

    assert _content_hash(
        composed,
        [_resolved_ingredient(quantity=100)],
    ) == _content_hash(
        decomposed,
        [_resolved_ingredient(quantity=100.00004, unit=" G ")],
    )


def test_content_hash_is_invariant_to_ingredient_order():
    recipe = {
        "name": "Rice Breakfast",
        "cuisine": "vietnamese",
        "meal_types": ["breakfast"],
    }

    assert _content_hash(
        recipe,
        [
            _resolved_ingredient(food_reference_id=7, quantity=100),
            _resolved_ingredient(food_reference_id=8, quantity=25, unit="ml"),
        ],
    ) == _content_hash(
        recipe,
        [
            _resolved_ingredient(food_reference_id=8, quantity=25, unit="ml"),
            _resolved_ingredient(food_reference_id=7, quantity=100),
        ],
    )


@pytest.mark.asyncio
async def test_near_duplicate_is_withheld_for_review_and_not_inserted():
    importer = _Importer(refs_by_id={7: _reference()})
    importer.session.signatures = [
        SimpleNamespace(
            catalog_key="existing-rice",
            content_hash="different",
            normalized_name="rice breakfast",
            normalized_cuisine="vietnamese",
            food_reference_ids=frozenset({7}),
        )
    ]

    summary = await importer.import_manifest(_manifest())

    assert summary.inserted == 0
    assert summary.review_required[0].reason == "near_duplicate"
    assert importer.session.added == []


@pytest.mark.asyncio
async def test_import_locks_before_writing_seed_meals():
    importer = _Importer(refs_by_id={7: _reference()})

    summary = await importer.import_manifest(_manifest())

    assert summary.inserted == 1
    assert importer.session.locked is True


@pytest.mark.asyncio
async def test_import_metrics_are_bounded_and_do_not_include_catalog_content():
    metrics = _Metrics()
    set_observability_connector_for_test(metrics)
    importer = _Importer(refs_by_id={7: _reference()})

    await importer.import_manifest(_manifest())

    metric_names = [call[1] for call in metrics.calls]
    assert metric_names == [
        "meal_catalog.seed_import.duration_ms",
        "meal_catalog.seed_import.imported",
        "meal_catalog.seed_import.skipped",
        "meal_catalog.seed_import.review_required",
        "meal_catalog.seed_import.rejected",
    ]
    for call in metrics.calls:
        assert call[4] == {"operation": "seed_import", "status": "success"}
    assert "rice-breakfast" not in str(metrics.calls)
    assert "Rice Breakfast" not in str(metrics.calls)
    assert "content_hash" not in str(metrics.calls)


@pytest.mark.asyncio
async def test_import_allows_unmapped_ingredients_when_enabled():
    manifest = {
        "recipes": [
            {
                "recipe_key": "vn-dragonfruit-snack",
                "cuisine": "vietnamese",
                "name": "Dragonfruit Snack",
                "meal_types": ["snack"],
                "ingredients": [
                    {
                        "name": "Thanh Long Đỏ",
                        "quantity": 150.0,
                        "unit": "g",
                        "category": "produce",
                    }
                ],
            }
        ]
    }
    importer = _Importer(allow_unmapped_ingredients=True)
    summary = await importer.import_manifest(manifest)

    assert summary.is_successful is True
    assert summary.inserted == 1
    assert len(importer.session.added) == 1
    seed = importer.session.added[0]
    assert seed.catalog_key == "vn-dragonfruit-snack"
    assert len(seed.ingredients) == 1
    assert seed.ingredients[0].food_reference_id is None
    assert seed.ingredients[0].display_name == "Thanh Long Đỏ"
    assert seed.ingredients[0].quantity == 150.0
    assert seed.ingredients[0].category == "produce"
