"""Migration script to copy images from Cloudinary to Cloudflare Images.

Usage:
    python scripts/migrate_cloudinary_to_cloudflare.py                 # Dry-run
    python scripts/migrate_cloudinary_to_cloudflare.py --execute       # Real execution
    python scripts/migrate_cloudinary_to_cloudflare.py --execute --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import select, update

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.infra.adapters.cloudflare_image_store import CloudflareImageStore
from src.infra.database.models.meal.meal_image import MealImageORM
from src.infra.database.uow_async import AsyncUnitOfWork

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def migrate_images(
    *,
    dry_run: bool = True,
    limit: int | None = None,
    batch_size: int = 50,
) -> dict[str, int]:
    cf_store = CloudflareImageStore()
    stats = {"total_found": 0, "migrated": 0, "failed": 0, "skipped": 0}

    async with AsyncUnitOfWork() as uow:
        session = uow.session
        if session is None:
            raise RuntimeError("Database session not initialized")

        stmt = select(MealImageORM).where(MealImageORM.url.like("%res.cloudinary.com%"))
        if limit:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        images = list(result.scalars().all())
        stats["total_found"] = len(images)
        logger.info("Found %d images to migrate", len(images))

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            for idx, img in enumerate(images, start=1):
                image_id = img.image_id
                old_url = img.url
                if not old_url:
                    stats["skipped"] += 1
                    continue

                logger.info(
                    "[%d/%d] Migrating image_id=%s from %s",
                    idx,
                    len(images),
                    image_id,
                    old_url,
                )

                if dry_run:
                    stats["migrated"] += 1
                    continue

                try:
                    # 1. Download from Cloudinary
                    resp = await http_client.get(old_url)
                    if resp.status_code != 200:
                        logger.error(
                            "Failed to download image %s from Cloudinary (status %d)",
                            image_id,
                            resp.status_code,
                        )
                        stats["failed"] += 1
                        continue

                    # Determine content type
                    content_type = (
                        resp.headers.get("content-type")
                        or f"image/{img.format or 'jpeg'}"
                    )

                    # 2. Upload to Cloudflare
                    new_url = await cf_store.save_async(
                        resp.content,
                        content_type=content_type,
                        image_id=image_id,
                    )

                    # 3. Update DB
                    await session.execute(
                        update(MealImageORM)
                        .where(MealImageORM.image_id == image_id)
                        .values(url=new_url)
                    )
                    stats["migrated"] += 1
                    logger.info("  -> Migrated to %s", new_url)

                    # Rate limit slightly
                    await asyncio.sleep(0.05)

                    if idx % batch_size == 0:
                        await uow.commit()
                        logger.info("Committed batch of %d images", idx)

                except Exception as exc:
                    logger.error("Failed to migrate image %s: %s", image_id, exc)
                    stats["failed"] += 1

        if not dry_run:
            await uow.commit()
            logger.info("Migration complete and committed to database.")
        else:
            logger.info("Dry-run complete. No changes were committed.")

    return stats


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(
        description="Migrate images from Cloudinary to Cloudflare Images"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute the migration (default: dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of images to migrate",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch commit size (default: 50)",
    )

    args = parser.parse_args()
    dry_run = not args.execute
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    logger.info("Starting Cloudinary -> Cloudflare migration in %s mode", mode)

    stats = asyncio.run(
        migrate_images(dry_run=dry_run, limit=args.limit, batch_size=args.batch_size)
    )
    logger.info("Migration stats: %s", stats)


if __name__ == "__main__":
    main()
