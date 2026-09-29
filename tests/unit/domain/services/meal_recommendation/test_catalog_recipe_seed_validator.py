from src.domain.services.meal_recommendation.catalog_recipe_seed_validator import (
    validate_catalog_seed_manifest,
)


def _recipe(
    recipe_key: str,
    cuisine: str,
    meal_type: str,
) -> dict:
    return {
        "recipe_key": recipe_key,
        "cuisine": cuisine,
        "name": f"{cuisine} {meal_type}",
        "meal_types": [meal_type],
        "ingredients": [
            {
                "food_reference_id": 7,
                "name": "Rice",
                "quantity": 100,
                "unit": "g",
            }
        ],
    }


def _complete_manifest() -> dict:
    recipes = []
    for cuisine in ("vietnamese", "japanese", "korean"):
        for meal_type in ("breakfast", "lunch", "dinner"):
            recipes.append(_recipe(f"{cuisine}-{meal_type}", cuisine, meal_type))
    return {
        "release_key": "test-release",
        "expected_recipe_count": len(recipes),
        "recipes": recipes,
    }


def test_valid_manifest_reports_digest_and_coverage():
    manifest = _complete_manifest()

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=9,
        min_per_cuisine_meal_type=1,
        expected_cuisine_counts={
            "vietnamese": 3,
            "japanese": 3,
            "korean": 3,
        },
    )

    assert result.is_valid is True
    assert len(result.manifest_digest) == 64
    assert result.recipe_count == 9
    assert result.coverage["vietnamese"]["breakfast"] == 1


def test_manifest_rejects_wrong_recipe_count_and_coverage():
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 180,
        "recipes": [_recipe("vn-breakfast", "vietnamese", "breakfast")],
    }

    result = validate_catalog_seed_manifest(manifest)

    assert result.is_valid is False
    assert "recipe count must be 180, got 1" in result.errors
    assert "cuisine japanese requires 60 recipes, got 0" in result.errors
    assert any("coverage japanese/lunch" in error for error in result.errors)


def test_manifest_can_allow_partial_file_with_final_expected_count_declared():
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 180,
        "recipes": [_recipe("vn-breakfast", "vietnamese", "breakfast")],
    }

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
        allow_declared_expected_count_mismatch=True,
    )

    assert result.is_valid is True


def test_manifest_allows_lightweight_product_team_schema_without_rights_or_sources():
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [_recipe("vn-breakfast", "vietnamese", "breakfast")],
    }

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is True


def test_manifest_allows_null_food_reference_id_for_import_time_lookup():
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [_recipe("vn-breakfast", "vietnamese", "breakfast")],
    }
    manifest["recipes"][0]["ingredients"][0]["food_reference_id"] = None

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is True


def test_manifest_allows_snack_meal_type():
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [_recipe("vn-snack", "vietnamese", "snack")],
    }

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is True
    assert result.coverage["vietnamese"]["snack"] == 1


def test_partial_manifest_allows_additional_cuisines_and_incomplete_coverage():
    manifest = {
        "release_key": "bootstrap-release",
        "expected_recipe_count": 3,
        "recipes": [
            _recipe("jp-breakfast", "japanese", "breakfast"),
            _recipe("western-lunch", "western", "lunch"),
            _recipe("international-dinner", "international", "dinner"),
        ],
    }

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=3,
        expected_cuisine_counts=None,
        min_per_cuisine_meal_type=5,
        allowed_cuisines=None,
        required_cuisines=(),
    )

    assert result.is_valid is True
    assert result.coverage["western"]["lunch"] == 1
    assert result.coverage["international"]["dinner"] == 1


def test_manifest_rejects_invalid_food_reference_id_type():
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [_recipe("vn-breakfast", "vietnamese", "breakfast")],
    }
    ingredient = manifest["recipes"][0]["ingredients"][0]
    ingredient["food_reference_id"] = "rice"

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is False
    assert any(
        "food_reference_id must be an integer or null" in error
        for error in result.errors
    )


def test_manifest_rejects_derived_recipe_fields():
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [_recipe("vn-breakfast", "vietnamese", "breakfast")],
    }
    manifest["recipes"][0]["servings"] = 1
    manifest["recipes"][0]["protein_g"] = 99

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is False
    assert any("servings is derived by backend" in error for error in result.errors)
    assert any("protein_g is derived by backend" in error for error in result.errors)


def test_manifest_rejects_invalid_popularity_rank():
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [_recipe("vn-breakfast", "vietnamese", "breakfast")],
    }
    manifest["recipes"][0]["popularity_rank"] = 2_147_483_648

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is False
    assert (
        "popularity_rank must fit a non-negative PostgreSQL INTEGER or null"
        in result.errors[0]
    )


def test_manifest_rejects_derived_ingredient_fields():
    recipe = _recipe("vn-breakfast", "vietnamese", "breakfast")
    recipe["ingredients"][0]["resolved_grams"] = 100
    recipe["ingredients"][0]["fiber_g"] = 1
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [recipe],
    }

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is False
    assert any(
        "resolved_grams is derived by backend" in error for error in result.errors
    )
    assert any("fiber_g is derived by backend" in error for error in result.errors)


def test_manifest_allows_fresh_produce_and_semantic_category_aliases():
    recipe = _recipe("vn-breakfast", "vietnamese", "breakfast")
    recipe["ingredients"][0]["category"] = "fresh_produce"
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [recipe],
    }

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is True
    assert result.errors == ()


def test_manifest_rejects_unknown_ingredient_category():
    recipe = _recipe("vn-breakfast", "vietnamese", "breakfast")
    recipe["ingredients"][0]["category"] = "unsupported_xyz_category"
    manifest = {
        "release_key": "test-release",
        "expected_recipe_count": 1,
        "recipes": [recipe],
    }

    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=1,
        min_per_cuisine_meal_type=0,
        expected_cuisine_counts=None,
    )

    assert result.is_valid is False
    assert any("ingredients[0].category is invalid" in error for error in result.errors)
