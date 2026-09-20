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


async def migrate_single_image(
    img: MealImageORM,
    *,
    cf_store: CloudflareImageStore,
    session,
    http_client: httpx.AsyncClient,
    dry_run: bool = False,
) -> bool:
    """Migrate a single image from Cloudinary to Cloudflare.

    Returns True if successfully migrated, False if failed.
    """
    image_id = img.image_id
    old_url = img.url
    if not old_url:
        return False

    if dry_run:
        return True

    content_type = "unknown"
    try:
        # 1. Download from Cloudinary
        resp = await http_client.get(old_url)
        if resp.status_code != 200:
            logger.error(
                "Failed to download image %s from Cloudinary (status %d)",
                image_id,
                resp.status_code,
            )
            return False

        # Determine content type with robust normalization and fallback
        raw_content_type = (
            resp.headers.get("content-type")
            or f"image/{getattr(img, 'format', None) or 'jpeg'}"
        )
        content_type = raw_content_type.split(";")[0].strip().lower()
        if content_type == "image/jpg":
            content_type = "image/jpeg"

        valid_mime_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        if content_type not in valid_mime_types:
            db_fmt = (getattr(img, "format", None) or "").lower().strip(".")
            if db_fmt in ("jpg", "jpeg"):
                content_type = "image/jpeg"
            elif db_fmt in ("png", "webp", "gif"):
                content_type = f"image/{db_fmt}"
            else:
                content_type = "image/jpeg"
            logger.warning(
                "Non-standard Content-Type '%s' for image %s; normalized to '%s'",
                raw_content_type,
                image_id,
                content_type,
            )

        # 2. Upload to Cloudflare
        try:
            new_url = await cf_store.save_async(
                resp.content,
                content_type=content_type,
                image_id=image_id,
            )
        except RuntimeError as upload_err:
            if "already exists" in str(upload_err) or "5409" in str(upload_err):
                logger.info(
                    "  -> Image %s already exists in Cloudflare; reusing delivery URL",
                    image_id,
                )
                new_url = cf_store.get_url(image_id)
            else:
                raise

        if not new_url or not new_url.strip() or not new_url.startswith("http"):
            logger.error(
                "Cloudflare upload returned invalid or empty URL for image %s: '%s'; skipping DB update",
                image_id,
                new_url,
            )
            return False

        # 3. Update DB
        await session.execute(
            update(MealImageORM)
            .where(MealImageORM.image_id == image_id)
            .values(url=new_url)
        )
        logger.info("  -> Migrated to %s", new_url)
        return True
    except Exception as exc:
        logger.error(
            "Failed to migrate image %s (url=%s, content_type=%s): %s",
            image_id,
            old_url,
            content_type,
            exc,
            exc_info=True,
        )
        return False


async def migrate_images(
    *,
    dry_run: bool = True,
    limit: int | None = None,
    batch_size: int = 50,
    cloud_name: str | None = None,
) -> dict[str, int]:
    cf_store = CloudflareImageStore()
    stats = {"total_found": 0, "migrated": 0, "failed": 0, "skipped": 0}

    async with AsyncUnitOfWork() as uow:
        session = uow.session
        if session is None:
            raise RuntimeError("Database session not initialized")

        stmt = select(MealImageORM).where(MealImageORM.url.like("%res.cloudinary.com%"))
        if cloud_name:
            stmt = stmt.where(
                MealImageORM.url.like(f"%res.cloudinary.com/{cloud_name}/%")
            )
        if limit:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        images = list(result.scalars().all())
        stats["total_found"] = len(images)
        logger.info("Found %d images to migrate", len(images))

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            for idx, img in enumerate(images, start=1):
                if not img.url:
                    stats["skipped"] += 1
                    continue

                logger.info(
                    "[%d/%d] Migrating image_id=%s from %s",
                    idx,
                    len(images),
                    img.image_id,
                    img.url,
                )

                success = await migrate_single_image(
                    img,
                    cf_store=cf_store,
                    session=session,
                    http_client=http_client,
                    dry_run=dry_run,
                )
                if success:
                    stats["migrated"] += 1
                else:
                    stats["failed"] += 1

                if not dry_run:
                    await asyncio.sleep(0.05)
                    if idx % batch_size == 0:
                        await uow.commit()
                        logger.info("Committed batch of %d images", idx)

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
    parser.add_argument(
        "--cloud-name",
        type=str,
        default=None,
        help="Only migrate images belonging to a specific Cloudinary cloud name (e.g. n6kanljt)",
    )

    args = parser.parse_args()
    dry_run = not args.execute
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    if args.cloud_name:
        logger.info(
            "Starting Cloudinary -> Cloudflare migration in %s mode (filtering cloud_name=%s)",
            mode,
            args.cloud_name,
        )
    else:
        logger.info("Starting Cloudinary -> Cloudflare migration in %s mode", mode)

    stats = asyncio.run(
        migrate_images(
            dry_run=dry_run,
            limit=args.limit,
            batch_size=args.batch_size,
            cloud_name=args.cloud_name,
        )
    )
    logger.info("Migration stats: %s", stats)


if __name__ == "__main__":
    main()
