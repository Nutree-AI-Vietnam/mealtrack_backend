"""CLI script to batch-enrich catalog recipes with micronutrients into PostgreSQL.

Usage:
    python scripts/enrich_catalog_recipe_micronutrients.py --limit 10
    python scripts/enrich_catalog_recipe_micronutrients.py --batch-size 8
    python scripts/enrich_catalog_recipe_micronutrients.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from src.api.dependencies.event_bus import (  # noqa: E402
    _catalog_recipe_batch_micronutrient_estimator,
    _catalog_recipe_fdc_micronutrient_loader,
    _catalog_recipe_micronutrient_estimator,
)
from src.app.services.catalog_recipe_micronutrient_enrichment_service import (  # noqa: E402
    CatalogRecipeMicronutrientEnrichmentService,
)
from src.infra.database.models.meal_recommendation import (  # noqa: E402
    MealCatalogMicronutrientEnrichmentORM,
    MealCatalogORM,
)
from src.infra.database.uow_async import AsyncUnitOfWork  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def run_enrichment(
    *,
    batch_size: int = 8,
    limit: int | None = None,
    concurrency: int = 3,
    cuisine: str | None = None,
    dry_run: bool = False,
) -> None:
    async with AsyncUnitOfWork() as uow:
        assert uow.session is not None
        # Check current stats
        total_active = await uow.session.scalar(
            select(func.count(MealCatalogORM.id)).where(
                MealCatalogORM.is_active.is_(True)
            )
        )
        ready_count = await uow.session.scalar(
            select(func.count(MealCatalogMicronutrientEnrichmentORM.id)).where(
                MealCatalogMicronutrientEnrichmentORM.status == "ready"
            )
        )

        # Find active recipes without ready enrichment
        ready_subquery = (
            select(MealCatalogMicronutrientEnrichmentORM.catalog_meal_id)
            .where(MealCatalogMicronutrientEnrichmentORM.status == "ready")
            .scalar_subquery()
        )

        query = (
            select(MealCatalogORM.id, MealCatalogORM.name, MealCatalogORM.cuisine)
            .where(
                MealCatalogORM.is_active.is_(True),
                MealCatalogORM.id.not_in(ready_subquery),
            )
            .order_by(
                MealCatalogORM.popularity_rank.asc().nulls_last(),
                MealCatalogORM.id.asc(),
            )
        )

        if cuisine:
            query = query.where(
                func.lower(MealCatalogORM.cuisine) == cuisine.strip().casefold()
            )

        if limit:
            query = query.limit(limit)

        result = await uow.session.execute(query)
        candidates = result.all()

    logger.info("=== Catalog Micronutrient Status ===")
    logger.info("Total active recipes: %d", total_active or 0)
    logger.info("Already enriched (ready): %d", ready_count or 0)
    logger.info("Candidates to enrich: %d", len(candidates))

    if dry_run:
        logger.info("Dry run complete. No recipes enriched.")
        return

    if not candidates:
        logger.info("All active recipes are already enriched! Nothing to do.")
        return

    service = CatalogRecipeMicronutrientEnrichmentService(
        AsyncUnitOfWork,
        estimator=_catalog_recipe_micronutrient_estimator,
        batch_estimator=_catalog_recipe_batch_micronutrient_estimator,
        fdc_loader=_catalog_recipe_fdc_micronutrient_loader,
    )

    candidate_ids = [row[0] for row in candidates]
    total_to_process = len(candidate_ids)
    chunks = [
        (idx + 1, candidate_ids[i : i + batch_size])
        for idx, i in enumerate(range(0, total_to_process, batch_size))
    ]
    total_batches = len(chunks)

    semaphore = asyncio.Semaphore(concurrency)
    succeeded_recipes = 0
    failed_recipes = 0
    completed_lock = asyncio.Lock()
    start_time = time.perf_counter()

    async def process_batch(batch_num: int, chunk: list[str]) -> None:
        nonlocal succeeded_recipes, failed_recipes
        async with semaphore:
            logger.info(
                "Processing batch %d/%d (%d recipes)...",
                batch_num,
                total_batches,
                len(chunk),
            )
            batch_start = time.perf_counter()
            try:
                success = await service.enrich_recipe_ids(chunk)
                batch_elapsed = time.perf_counter() - batch_start
                async with completed_lock:
                    if success:
                        succeeded_recipes += len(chunk)
                    else:
                        failed_recipes += len(chunk)
                    done_count = succeeded_recipes + failed_recipes
                logger.info(
                    "Batch %d/%d finished in %.2fs (success=%s) [%d/%d total]",
                    batch_num,
                    total_batches,
                    batch_elapsed,
                    success,
                    done_count,
                    total_to_process,
                )
            except Exception:
                logger.exception("Batch %d failed with error", batch_num)
                async with completed_lock:
                    failed_recipes += len(chunk)

    logger.info(
        "Starting enrichment: %d recipes across %d batches (concurrency=%d)...",
        total_to_process,
        total_batches,
        concurrency,
    )

    await asyncio.gather(*(process_batch(b_num, ch) for b_num, ch in chunks))

    elapsed_total = time.perf_counter() - start_time

    # Final DB check
    async with AsyncUnitOfWork() as uow:
        assert uow.session is not None
        final_ready = await uow.session.scalar(
            select(func.count(MealCatalogMicronutrientEnrichmentORM.id)).where(
                MealCatalogMicronutrientEnrichmentORM.status == "ready"
            )
        )

    logger.info("==================================================")
    logger.info("Enrichment run complete:")
    logger.info("  Total batches: %d", total_batches)
    logger.info("  Total succeeded recipes: %d", succeeded_recipes)
    logger.info("  Total failed recipes: %d", failed_recipes)
    logger.info(
        "  Total time: %.2fs (avg %.2fs/recipe)",
        elapsed_total,
        elapsed_total / max(1, total_to_process),
    )
    logger.info(
        "  Now ready in DB: %d / %d recipes", final_ready or 0, total_active or 0
    )
    logger.info("==================================================")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-enrich catalog recipes with micronutrients."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Number of recipes to batch per AI estimation call (default: 8).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Number of concurrent batches to process (default: 3).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of recipes to enrich.",
    )
    parser.add_argument(
        "--cuisine",
        type=str,
        default=None,
        help="Filter recipes by cuisine.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect counts without calling AI or modifying the database.",
    )

    args = parser.parse_args()
    asyncio.run(
        run_enrichment(
            batch_size=args.batch_size,
            limit=args.limit,
            concurrency=args.concurrency,
            cuisine=args.cuisine,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
