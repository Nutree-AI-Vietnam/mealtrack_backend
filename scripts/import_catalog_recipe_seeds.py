"""Validate and import catalog recipe seed manifests."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.services.catalog_meal_seed_import_service import CatalogMealSeedImporter
from src.domain.services.meal_recommendation.catalog_recipe_seed_validator import (
    PRODUCTION_CUISINE_COUNTS,
    REQUIRED_CUISINES,
    CatalogSeedValidationResult,
    validate_catalog_seed_manifest,
)
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate meal recommendation catalog recipe seeds."
    )
    parser.add_argument(
        "--manifest",
        default=str(
            Path(__file__).resolve().parent
            / "data"
            / "meal-recommendation-recipes.json"
        ),
        help="Catalog recipe seed manifest JSON path.",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        default=180,
        help="Expected recipe count. Production default is 180.",
    )
    parser.add_argument(
        "--min-per-cuisine-meal-type",
        type=int,
        default=5,
        help="Minimum eligible recipes for each cuisine and meal type.",
    )
    parser.add_argument(
        "--skip-exact-cuisine-count",
        action="store_true",
        help="Disable production 60/60/60 cuisine split validation for fixtures.",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help=(
            "Validate/import a partial manifest sample using its actual recipe count. "
            "Disables the exact production cuisine split."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and resolve only. Does not write to the database.",
    )
    parser.add_argument(
        "--resolver-report",
        default=None,
        help="Write unresolved ingredient candidates to this JSON path.",
    )
    parser.add_argument(
        "--resolver-map",
        default=None,
        help="Approved ingredient mapping JSON: ingredient name/normalized name -> food_reference_id.",
    )
    parser.add_argument(
        "--auto-resolve-threshold",
        type=float,
        default=None,
        help="Minimum fuzzy score for automatic verified candidate selection.",
    )
    parser.add_argument(
        "--resolve-all-best-effort",
        action="store_true",
        help=(
            "Bootstrap mode: choose the best candidate even when unverified or "
            "from an unapproved source."
        ),
    )
    parser.add_argument(
        "--allow-unmapped-ingredients",
        action="store_true",
        default=True,
        help="Allow ingredients without food_reference_id (default: True).",
    )
    parser.add_argument(
        "--strict-food-reference",
        action="store_true",
        help="Disallow ingredients without food_reference_id.",
    )
    parser.add_argument(
        "--skip-failed-recipes",
        action="store_true",
        help="Skip individual recipes that fail ingredient resolution and import only the clean ones.",
    )
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite existing catalog recipes if content_hash differs.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip recipes whose catalog_key already exists in the catalog.",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    expected_count = (
        len(manifest.get("recipes", [])) if args.partial else args.expected_count
    )
    result = validate_catalog_seed_manifest(
        manifest,
        expected_recipe_count=expected_count,
        min_per_cuisine_meal_type=args.min_per_cuisine_meal_type,
        expected_cuisine_counts=(
            None
            if args.partial or args.skip_exact_cuisine_count
            else PRODUCTION_CUISINE_COUNTS
        ),
        allow_declared_expected_count_mismatch=args.partial,
        allowed_cuisines=None if args.partial else REQUIRED_CUISINES,
        required_cuisines=() if args.partial else REQUIRED_CUISINES,
    )

    print(f"manifest_digest={result.manifest_digest}")
    print(f"recipe_count={result.recipe_count}")
    for cuisine, meal_counts in sorted(result.coverage.items()):
        print(f"coverage[{cuisine}]={meal_counts}")

    if result.errors:
        print("validation=failed")
        for error in result.errors:
            print(f"- {error}", file=sys.stderr)
        sys.exit(1)

    print("validation=passed")
    approved_mappings = _load_resolver_map(args.resolver_map)
    auto_resolve_threshold = (
        0.0
        if args.resolve_all_best_effort and args.auto_resolve_threshold is None
        else (
            0.92 if args.auto_resolve_threshold is None else args.auto_resolve_threshold
        )
    )
    allow_unmapped_ingredients = not args.strict_food_reference
    summary = asyncio.run(
        _run_import(
            manifest,
            dry_run=args.dry_run,
            approved_mappings=approved_mappings,
            auto_resolve_threshold=auto_resolve_threshold,
            resolve_all_best_effort=args.resolve_all_best_effort,
            allow_unmapped_ingredients=allow_unmapped_ingredients,
            skip_failed_recipes=args.skip_failed_recipes,
            overwrite_existing=args.overwrite_existing,
            skip_existing=args.skip_existing,
        )
    )
    if args.resolver_report:
        report_path = Path(args.resolver_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                _build_import_report(validation=result, summary=summary),
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"resolver_report={report_path}")
    for error in summary.errors:
        print(f"- {error}", file=sys.stderr)
    print(f"db_import={'dry_run' if summary.dry_run else 'applied'}")
    print(f"inserted={summary.inserted}")
    print(f"updated={summary.updated}")
    print(f"skipped_existing={summary.skipped_existing}")
    if not summary.is_successful:
        print("import=failed")
        sys.exit(1)
    print("import=passed")


def _load_resolver_map(path: str | None) -> dict[str, int]:
    if path is None:
        return {}
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("resolver map must be a JSON object")
    return {str(key): int(value) for key, value in data.items()}


def _build_import_report(
    *,
    validation: CatalogSeedValidationResult,
    summary,
) -> dict:
    report = summary.resolution_report()
    report["manifest_digest"] = validation.manifest_digest
    report["recipe_count"] = validation.recipe_count
    report["coverage"] = {
        cuisine: dict(sorted(meal_counts.items()))
        for cuisine, meal_counts in sorted(validation.coverage.items())
    }
    report["validation_errors"] = list(validation.errors)
    return report


async def _run_import(
    manifest: dict,
    *,
    dry_run: bool,
    approved_mappings: dict[str, int],
    auto_resolve_threshold: float,
    resolve_all_best_effort: bool,
    allow_unmapped_ingredients: bool = True,
    skip_failed_recipes: bool = False,
    overwrite_existing: bool = False,
    skip_existing: bool = False,
):
    async with AsyncUnitOfWork() as uow:
        if uow.session is None:
            raise RuntimeError("AsyncUnitOfWork did not initialize a database session")
        await uow.session.execute(text("select 1"))
        catalog_repository = AsyncCatalogMealRepository(uow.session)
        food_reference_repository = AsyncFoodReferenceRepository(uow.session)

        target_manifest = manifest
        if skip_failed_recipes:
            filter_importer = CatalogMealSeedImporter(
                catalog_repository,
                food_reference_repository,
                dry_run=True,
                approved_mappings=approved_mappings,
                auto_resolve_threshold=auto_resolve_threshold,
                resolve_all_best_effort=resolve_all_best_effort,
                allow_unmapped_ingredients=allow_unmapped_ingredients,
                overwrite_existing=overwrite_existing,
                skip_existing=skip_existing,
            )
            clean_recipes = []
            for idx, r in enumerate(manifest.get("recipes", [])):
                try:
                    prep = await filter_importer._prepare_recipe(r, idx)
                    if prep is not None:
                        clean_recipes.append(r)
                except Exception:
                    continue
            print(
                f"skip_failed_recipes: filtered {len(clean_recipes)} / {len(manifest.get('recipes', []))} clean recipes."
            )
            target_manifest = dict(manifest)
            target_manifest["recipes"] = clean_recipes
            target_manifest["expected_recipe_count"] = len(clean_recipes)

        preview = await CatalogMealSeedImporter(
            catalog_repository,
            food_reference_repository,
            dry_run=True,
            approved_mappings=approved_mappings,
            auto_resolve_threshold=auto_resolve_threshold,
            resolve_all_best_effort=resolve_all_best_effort,
            allow_unmapped_ingredients=allow_unmapped_ingredients,
            overwrite_existing=overwrite_existing,
            skip_existing=skip_existing,
        ).import_manifest(target_manifest)
        if dry_run or not preview.is_successful:
            return preview
        summary = await CatalogMealSeedImporter(
            catalog_repository,
            food_reference_repository,
            dry_run=False,
            approved_mappings=approved_mappings,
            auto_resolve_threshold=auto_resolve_threshold,
            resolve_all_best_effort=resolve_all_best_effort,
            allow_unmapped_ingredients=allow_unmapped_ingredients,
            overwrite_existing=overwrite_existing,
            skip_existing=skip_existing,
        ).import_manifest(target_manifest)
        if not summary.is_successful:
            await uow.rollback()
        return summary


if __name__ == "__main__":
    main()
