"""Validation helpers for catalog recipe seed manifests."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from src.domain.ports.catalog_recipe_repository_port import MAX_CATALOG_POPULARITY_RANK

REQUIRED_CUISINES = ("vietnamese", "japanese", "korean")
ALLOWED_MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
REQUIRED_COVERAGE_MEAL_TYPES = ("breakfast", "lunch", "dinner")
PRODUCTION_CUISINE_COUNTS = {
    "vietnamese": 60,
    "japanese": 60,
    "korean": 60,
}
_DERIVED_RECIPE_FIELDS = frozenset(
    {
        "servings",
        "instructions",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "fiber_g",
        "sugar_g",
    }
)
_DERIVED_INGREDIENT_FIELDS = frozenset(
    {"resolved_grams", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g"}
)


@dataclass(frozen=True)
class CatalogSeedValidationResult:
    """Validation report for a catalog recipe manifest."""

    manifest_digest: str
    recipe_count: int
    errors: tuple[str, ...] = field(default_factory=tuple)
    coverage: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_catalog_seed_manifest(
    manifest: dict[str, Any],
    *,
    expected_recipe_count: int = 180,
    min_per_cuisine_meal_type: int = 5,
    expected_cuisine_counts: dict[str, int] | None = PRODUCTION_CUISINE_COUNTS,
    allow_declared_expected_count_mismatch: bool = False,
    allowed_cuisines: Collection[str] | None = REQUIRED_CUISINES,
    required_cuisines: Collection[str] | None = REQUIRED_CUISINES,
) -> CatalogSeedValidationResult:
    """Validate catalog recipe seed manifest before any DB writes.

    Production callers retain the strict cuisine contract. Partial imports may
    pass ``None`` for ``allowed_cuisines`` and an empty collection for
    ``required_cuisines`` because their source corpus can contain additional
    cuisine labels and intentionally incomplete coverage.
    """

    errors: list[str] = []
    recipes = manifest.get("recipes")
    if not isinstance(recipes, list):
        return CatalogSeedValidationResult(
            manifest_digest=manifest_digest(manifest),
            recipe_count=0,
            errors=("recipes must be an array",),
        )

    declared_expected = manifest.get("expected_recipe_count", expected_recipe_count)
    if (
        declared_expected != expected_recipe_count
        and not allow_declared_expected_count_mismatch
    ):
        errors.append(
            f"expected_recipe_count must be {expected_recipe_count}, "
            f"got {declared_expected}"
        )
    if len(recipes) != expected_recipe_count:
        errors.append(
            f"recipe count must be {expected_recipe_count}, got {len(recipes)}"
        )

    keys: Counter[str] = Counter()
    cuisine_counts: Counter[str] = Counter()
    coverage: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for index, recipe in enumerate(recipes):
        if not isinstance(recipe, dict):
            errors.append(f"recipes[{index}] must be an object")
            continue
        _validate_recipe(
            recipe,
            index,
            errors,
            keys,
            coverage,
            cuisine_counts,
            allowed_cuisines,
        )

    for recipe_key, count in keys.items():
        if count > 1:
            errors.append(f"duplicate recipe_key: {recipe_key}")

    if expected_cuisine_counts is not None:
        for cuisine, expected_count in expected_cuisine_counts.items():
            actual_count = cuisine_counts[cuisine]
            if actual_count != expected_count:
                errors.append(
                    f"cuisine {cuisine} requires {expected_count} recipes, "
                    f"got {actual_count}"
                )

    coverage_dict = {
        cuisine: {
            meal_type: counts.get(meal_type, 0) for meal_type in ALLOWED_MEAL_TYPES
        }
        for cuisine, counts in coverage.items()
    }
    for cuisine in required_cuisines or ():
        for meal_type in REQUIRED_COVERAGE_MEAL_TYPES:
            count = coverage[cuisine][meal_type]
            if count < min_per_cuisine_meal_type:
                errors.append(
                    f"coverage {cuisine}/{meal_type} requires "
                    f"{min_per_cuisine_meal_type}, got {count}"
                )

    return CatalogSeedValidationResult(
        manifest_digest=manifest_digest(manifest),
        recipe_count=len(recipes),
        errors=tuple(errors),
        coverage=coverage_dict,
    )


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Return a stable SHA-256 digest for the manifest content."""

    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_recipe(
    recipe: dict[str, Any],
    index: int,
    errors: list[str],
    keys: Counter[str],
    coverage: defaultdict[str, Counter[str]],
    cuisine_counts: Counter[str],
    allowed_cuisines: Collection[str] | None,
) -> None:
    recipe_key = _string(recipe.get("recipe_key"))
    if recipe_key is None:
        errors.append(f"recipes[{index}].recipe_key is required")
    else:
        keys[recipe_key] += 1

    cuisine = _string(recipe.get("cuisine"))
    if cuisine is None or (
        allowed_cuisines is not None and cuisine not in allowed_cuisines
    ):
        errors.append(f"recipes[{index}].cuisine is invalid: {cuisine}")
    else:
        cuisine_counts[cuisine] += 1

    meal_types = recipe.get("meal_types")
    if not isinstance(meal_types, list) or not meal_types:
        errors.append(f"recipes[{index}].meal_types must be a non-empty array")
        meal_types = []
    for meal_type in meal_types:
        if meal_type not in ALLOWED_MEAL_TYPES:
            errors.append(f"recipes[{index}].meal_types has invalid value: {meal_type}")
        elif cuisine is not None:
            coverage[cuisine][meal_type] += 1

    if _string(recipe.get("name")) is None:
        errors.append(f"recipes[{index}].name is required")

    popularity_rank = recipe.get("popularity_rank")
    if popularity_rank is not None and (
        isinstance(popularity_rank, bool)
        or not isinstance(popularity_rank, int)
        or not 0 <= popularity_rank <= MAX_CATALOG_POPULARITY_RANK
    ):
        errors.append(
            f"recipes[{index}].popularity_rank must fit a non-negative PostgreSQL INTEGER or null"
        )

    _validate_absent_derived_recipe_fields(recipe, index, errors)
    _validate_ingredients(recipe.get("ingredients"), index, errors)
    _validate_recipe_detail(recipe, index, errors)


def _validate_ingredients(ingredients: Any, index: int, errors: list[str]) -> None:
    if not isinstance(ingredients, list) or not ingredients:
        errors.append(f"recipes[{index}].ingredients must be a non-empty array")
        return
    for ingredient_index, ingredient in enumerate(ingredients):
        if not isinstance(ingredient, dict):
            errors.append(
                f"recipes[{index}].ingredients[{ingredient_index}] must be an object"
            )
            continue
        food_reference_id = ingredient.get("food_reference_id")
        if food_reference_id is not None and not isinstance(food_reference_id, int):
            errors.append(
                f"recipes[{index}].ingredients[{ingredient_index}].food_reference_id "
                "must be an integer or null"
            )
        if _string(ingredient.get("name")) is None:
            errors.append(
                f"recipes[{index}].ingredients[{ingredient_index}].name is required"
            )
        quantity = ingredient.get("quantity")
        if not isinstance(quantity, int | float) or quantity <= 0:
            errors.append(
                f"recipes[{index}].ingredients[{ingredient_index}].quantity "
                "must be positive"
            )
        if _string(ingredient.get("unit")) is None:
            errors.append(
                f"recipes[{index}].ingredients[{ingredient_index}].unit is required"
            )
        if ingredient.get("category", "pantry") not in {"produce", "protein", "pantry"}:
            errors.append(
                f"recipes[{index}].ingredients[{ingredient_index}].category is invalid"
            )
        _validate_absent_derived_ingredient_fields(
            ingredient,
            index,
            ingredient_index,
            errors,
        )


def _validate_recipe_detail(
    recipe: dict[str, Any], index: int, errors: list[str]
) -> None:
    for field_name in ("prep_time_minutes", "cook_time_minutes"):
        value = recipe.get(field_name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            errors.append(
                f"recipes[{index}].{field_name} must be a non-negative integer"
            )
    steps = recipe.get("steps")
    if steps is None:
        return
    if not isinstance(steps, list) or len(steps) > 50:
        errors.append(f"recipes[{index}].steps must contain at most 50 items")
        return
    for step_number, step in enumerate(steps, start=1):
        if (
            not isinstance(step, dict)
            or step.get("step_number", step_number) != step_number
        ):
            errors.append(f"recipes[{index}].steps must be consecutively numbered")
            break
        if (
            _string(step.get("title")) is None
            or _string(step.get("description")) is None
        ):
            errors.append(
                f"recipes[{index}].steps[{step_number - 1}] requires title and description"
            )


def _validate_absent_derived_recipe_fields(
    recipe: dict[str, Any],
    index: int,
    errors: list[str],
) -> None:
    for key in sorted(_DERIVED_RECIPE_FIELDS.intersection(recipe)):
        errors.append(
            f"recipes[{index}].{key} is derived by backend and must not be supplied"
        )


def _validate_absent_derived_ingredient_fields(
    ingredient: dict[str, Any],
    recipe_index: int,
    ingredient_index: int,
    errors: list[str],
) -> None:
    for key in sorted(_DERIVED_INGREDIENT_FIELDS.intersection(ingredient)):
        errors.append(
            f"recipes[{recipe_index}].ingredients[{ingredient_index}].{key} "
            "is derived by backend and must not be supplied"
        )


def _string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
