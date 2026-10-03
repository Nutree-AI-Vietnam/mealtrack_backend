"""Import breakfast recipes from USDA FDC JSON manifest into staging/production database.

Normalizes categories (grain, dairy, condiment -> pantry), unpins external FDC IDs,
imports into meal_catalog, and stores pre-computed micronutrients in meal_catalog_micronutrient_enrichment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.services.catalog_meal_seed_import_service import CatalogMealSeedImporter
from src.domain.model.meal_recommendation.catalog_recipe import (
    ALLOWED_CATALOG_INGREDIENT_CATEGORIES,
    PRODUCE_CATEGORY_ALIASES,
    PROTEIN_CATEGORY_ALIASES,
)
from src.domain.services.meal_recommendation.catalog_recipe_seed_validator import (
    validate_catalog_seed_manifest,
)
from src.infra.database.models.meal_recommendation import MealCatalogORM
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = (
    ALLOWED_CATALOG_INGREDIENT_CATEGORIES
    | PRODUCE_CATEGORY_ALIASES
    | PROTEIN_CATEGORY_ALIASES
)

DEFAULT_STAGING_URL = (
    "postgresql+asyncpg://neondb_owner:npg_J0rl7UGaMzNB@ep-round-base-ani0tihw.c-6.us-east-1.aws.neon.tech/neondb?ssl=require"
)


def normalize_manifest(raw_manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, float]]]:
    """Clean category names and clear external FDC IDs, returning clean manifest and micros map."""
    cleaned_manifest = dict(raw_manifest)
    cleaned_recipes = []
    recipe_micros_map: dict[str, dict[str, float]] = {}

    for recipe in raw_manifest.get("recipes", []):
        r = dict(recipe)
        recipe_key = str(r["recipe_key"]).strip()

        # Save micronutrients map if present
        if "micronutrients" in r and isinstance(r["micronutrients"], dict):
            recipe_micros_map[recipe_key] = r["micronutrients"]

        cleaned_ingredients = []
        for ing in r.get("ingredients", []):
            item = dict(ing)
            cat_raw = str(item.get("category", "pantry")).strip().casefold()
            if cat_raw in ALLOWED_CATEGORIES:
                item["category"] = cat_raw
            else:
                # grain, dairy, condiment, etc. map to pantry
                item["category"] = "pantry"

            # Clear external FDC ID so it does not fail internal food_reference FK
            item["food_reference_id"] = None
            cleaned_ingredients.append(item)

        r["ingredients"] = cleaned_ingredients
        cleaned_recipes.append(r)

    cleaned_manifest["recipes"] = cleaned_recipes
    cleaned_manifest["expected_recipe_count"] = len(cleaned_recipes)
    return cleaned_manifest, recipe_micros_map


async def run_import(
    manifest_path: Path,
    db_url: str,
    *,
    dry_run: bool = True,
) -> None:
    logger.info("Reading manifest from: %s", manifest_path)
    with manifest_path.open("r", encoding="utf-8") as f:
        raw_manifest = json.load(f)

    cleaned_manifest, recipe_micros_map = normalize_manifest(raw_manifest)

    # Validate before DB operations
    validation = validate_catalog_seed_manifest(
        cleaned_manifest,
        expected_recipe_count=len(cleaned_manifest["recipes"]),
        min_per_cuisine_meal_type=1,
        expected_cuisine_counts=None,
        allow_declared_expected_count_mismatch=True,
        allowed_cuisines=None,
        required_cuisines=(),
    )

    if not validation.is_valid:
        logger.error("Validation failed with %d errors:", len(validation.errors))
        for err in validation.errors[:10]:
            logger.error("  - %s", err)
        sys.exit(1)

    logger.info("Manifest validation passed! Recipes to import: %d", len(cleaned_manifest["recipes"]))
    for cuisine, meal_types in sorted(validation.coverage.items()):
        logger.info("  Coverage [%s]: %s", cuisine, meal_types)

    # Mask password for logging
    safe_url = db_url.split("@")[-1] if "@" in db_url else db_url
    logger.info("Connecting to target database at: %s", safe_url)

    engine = create_async_engine(db_url, pool_pre_ping=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        catalog_repo = AsyncCatalogMealRepository(session)
        food_repo = AsyncFoodReferenceRepository(session)

        # 1. Import recipes into meal_catalog
        importer = CatalogMealSeedImporter(
            catalog_repo,
            food_repo,
            dry_run=dry_run,
            allow_unmapped_ingredients=True,
        )

        summary = await importer.import_manifest(cleaned_manifest)
        logger.info(
            "Meal catalog import: inserted=%d, updated=%d, skipped=%d, dry_run=%s",
            summary.inserted,
            summary.updated,
            summary.skipped_existing,
            dry_run,
        )

        if not summary.is_successful:
            logger.error("Catalog import encountered errors: %s", summary.errors)
            sys.exit(1)

        if dry_run:
            logger.info("Dry run complete. No changes committed.")
            return

        # 2. Enrich micronutrients in meal_catalog_micronutrient_enrichment
        keys = list(recipe_micros_map.keys())
        query = select(MealCatalogORM.id, MealCatalogORM.catalog_key, MealCatalogORM.content_hash).where(
            MealCatalogORM.catalog_key.in_(keys)
        )
        res = await session.execute(query)
        imported_rows = res.all()

        micros_inserted = 0
        for meal_id, catalog_key, content_hash in imported_rows:
            micros = recipe_micros_map.get(catalog_key, {})
            if not micros:
                continue

            sources = {k: "usda_fdc" for k in micros.keys()}
            await session.execute(
                text(
                    """
                    INSERT INTO meal_catalog_micronutrient_enrichment 
                        (id, catalog_meal_id, content_hash, micros, sources, status)
                    VALUES 
                        (:id, :catalog_meal_id, :content_hash, CAST(:micros AS jsonb), CAST(:sources AS jsonb), :status)
                    ON CONFLICT (catalog_meal_id, content_hash) DO NOTHING
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "catalog_meal_id": str(meal_id),
                    "content_hash": str(content_hash),
                    "micros": json.dumps(micros),
                    "sources": json.dumps(sources),
                    "status": "ready",
                },
            )
            micros_inserted += 1

        logger.info("Micronutrient enrichment records created: %d", micros_inserted)
        await session.commit()
        logger.info("Successfully committed all records to database!")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import breakfast recipes with USDA micronutrients")
    parser.add_argument(
        "--manifest",
        default="/Users/alexnguyen/Downloads/breakfast_100_usda_fdc.json",
        help="Path to breakfast manifest JSON file",
    )
    parser.add_argument(
        "--db-url",
        default=os.getenv("STAGING_DATABASE_URL", DEFAULT_STAGING_URL),
        help="Target PostgreSQL database URL (defaults to Staging Nutree on Neon)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to the database (defaults to dry-run if omitted)",
    )
    args = parser.parse_args()

    asyncio.run(
        run_import(
            Path(args.manifest),
            db_url=args.db_url,
            dry_run=not args.apply,
        )
    )


if __name__ == "__main__":
    main()
