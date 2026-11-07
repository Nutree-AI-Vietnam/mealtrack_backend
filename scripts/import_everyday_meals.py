"""Find 5–10 everyday meals and import them into the catalog.

The Saigon calendar day is the cap. A second run on the same day imports
nothing further. Dish names and the importer schema are not hand-written.

    python scripts/import_everyday_meals.py
    python scripts/import_everyday_meals.py --count 5 --market US --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.services.catalog_meal_seed_import_service import CatalogMealSeedImporter
from src.app.services.everyday_catalog_import_service import (
    EverydayCatalogImportService,
    EverydayImportResult,
)
from src.bootstrap.inline_catalog_preparation import prepare_pending_catalog
from src.domain.services.everyday_catalog import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    clamp_batch_size,
)
from src.domain.services.meal_suggestion.ingredient_nutrition_resolver import (
    IngredientNutritionResolver,
)
from src.infra.adapters.everyday_meal_drafter import EverydayMealDrafter
from src.infra.adapters.fat_secret_service import get_fat_secret_service
from src.infra.adapters.meal_generation_service import MealGenerationService
from src.infra.database.uow_async import AsyncUnitOfWork
from src.infra.repositories.catalog_recipe_repository_async import (
    AsyncCatalogMealRepository,
)
from src.infra.repositories.food_reference_repository_async import (
    AsyncFoodReferenceRepository,
)


class _Store:
    async def count_imported_since(self, source_name: str, since: datetime) -> int:
        async with AsyncUnitOfWork() as uow:
            repository = AsyncCatalogMealRepository(_session(uow))
            return await repository.count_by_source_since(source_name, since)

    async def import_manifest(self, manifest: dict, *, dry_run: bool):
        async with AsyncUnitOfWork() as uow:
            session = _session(uow)
            catalog_repository = AsyncCatalogMealRepository(session)
            food_reference_repository = AsyncFoodReferenceRepository(session)

            async def enrich(name: str) -> bool:
                resolver = IngredientNutritionResolver(
                    fatsecret=get_fat_secret_service(),
                    food_ref_repo=food_reference_repository,
                )
                return await resolver.resolve(name) is not None

            importer = CatalogMealSeedImporter(
                catalog_repository,
                food_reference_repository,
                dry_run=dry_run,
                resolve_all_best_effort=True,
                auto_resolve_threshold=0.0,
                allow_unmapped_ingredients=False,
                skip_existing=True,
                candidate_enricher=enrich,
            )
            await importer.enrich_missing_candidates(manifest)
            summary = await importer.import_manifest(manifest)
            if not summary.is_successful:
                await uow.rollback()
            return summary


def _session(uow: AsyncUnitOfWork):
    if uow.session is None:
        raise RuntimeError("AsyncUnitOfWork did not initialize a database session")
    return uow.session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.getenv("EVERYDAY_CATALOG_BATCH_SIZE", str(DEFAULT_BATCH_SIZE))),
        help=f"Meals to import today, from {MIN_BATCH_SIZE} to {MAX_BATCH_SIZE}.",
    )
    parser.add_argument(
        "--market",
        choices=("VN", "US"),
        default=None,
        help="Limit this run to one country. The default alternates both.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and validate without committing catalog rows.",
    )
    args = parser.parse_args()
    batch = clamp_batch_size(args.count)
    result = asyncio.run(
        EverydayCatalogImportService(
            EverydayMealDrafter(MealGenerationService()), _Store()
        ).run(
            now=datetime.now(UTC),
            batch_size=batch,
            market=args.market,
            dry_run=args.dry_run,
        )
    )
    _print(result)
    if result.inserted and not result.dry_run:
        prepared = asyncio.run(prepare_pending_catalog(max_jobs=result.inserted * 4))
        print(f"catalog_prepared_jobs={prepared}")


def _print(result: EverydayImportResult) -> None:
    print(f"day={result.day}")
    print(f"requested={result.requested}")
    print(f"already_imported={result.already_imported}")
    print(f"inserted={result.inserted}")
    print(f"skipped={result.skipped}")
    print(f"failed={result.failed}")
    print(f"dry_run={str(result.dry_run).lower()}")
    for name in result.names:
        print(f"meal={name}")


if __name__ == "__main__":
    main()
