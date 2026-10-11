"""
One-time cleanup script to mark orphaned meals as FAILED.

Orphaned meals have image_ids that don't exist in Cloudinary.

Usage:
    python scripts/cleanup_orphaned_meal_images.py          # Dry-run (default)
    python scripts/cleanup_orphaned_meal_images.py --execute  # Actually update DB
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import requests
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

BATCH_SIZE = 100
RATE_LIMIT_DELAY = 0.1  # 10 requests per second


def find_orphaned_meals(
    session,
    cloudinary,
    limit: int | None = None,
) -> list[str]:
    """
    Find meals where the Cloudinary image no longer exists.

    Returns list of orphaned meal_ids.
    """
    # Query all READY meals from scanner source
    query = text("""
        SELECT m.meal_id, m.image_id, m.dish_name, mi.url
        FROM meal m
        JOIN mealimage mi ON m.image_id = mi.image_id
        WHERE m.status = 'READY'
          AND m.source = 'scanner'
        ORDER BY m.created_at DESC
    """)

    if limit:
        query = text(f"""
            SELECT m.meal_id, m.image_id, m.dish_name, mi.url
            FROM meal m
            JOIN mealimage mi ON m.image_id = mi.image_id
            WHERE m.status = 'READY'
              AND m.source = 'scanner'
            ORDER BY m.created_at DESC
            LIMIT {limit}
        """)

    result = session.execute(query)
    candidates = result.fetchall()

    logger.info(f"Found {len(candidates)} candidate meals to check")

    orphans = []
    valid = 0

    for i, (meal_id, image_id, _dish_name, url) in enumerate(candidates):
        if i > 0 and i % 50 == 0:
            logger.info(
                f"Progress: {i}/{len(candidates)} checked, {len(orphans)} orphans found"
            )

        is_orphan = False

        if not url:
            # No URL stored - check Cloudinary API
            found_url = cloudinary.get_url(image_id)
            if found_url is None:
                is_orphan = True
                logger.info(
                    f"  - meal {meal_id}: image NOT FOUND (no URL, Cloudinary lookup failed)"
                )
        else:
            # URL exists - verify it's accessible
            try:
                response = requests.head(url, timeout=5)
                if response.status_code == 404:
                    is_orphan = True
                    logger.info(
                        f"  - meal {meal_id}: image NOT FOUND (URL returns 404)"
                    )
                elif response.status_code >= 400:
                    is_orphan = True
                    logger.info(
                        f"  - meal {meal_id}: image NOT FOUND (URL returns {response.status_code})"
                    )
            except requests.RequestException as e:
                # Network error - log but don't mark as orphan (might be temporary)
                logger.warning(f"  - meal {meal_id}: could not verify URL ({e})")
                continue

        if is_orphan:
            orphans.append(meal_id)
        else:
            valid += 1

        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

    logger.info("\nSummary:")
    logger.info(f"  Total checked: {len(candidates)}")
    logger.info(f"  Orphaned: {len(orphans)}")
    logger.info(f"  Valid: {valid}")

    return orphans


def mark_meals_failed(session, orphan_ids: list[str]) -> int:
    """
    Mark orphaned meals as FAILED.

    Returns count of updated meals.
    """
    if not orphan_ids:
        return 0

    # Update in batches
    updated = 0
    for i in range(0, len(orphan_ids), BATCH_SIZE):
        batch = orphan_ids[i : i + BATCH_SIZE]
        placeholders = ", ".join([f":id_{j}" for j in range(len(batch))])

        query = text(f"""
            UPDATE meal
            SET status = 'FAILED',
                error_message = 'Image missing from storage - data recovery not possible'
            WHERE meal_id IN ({placeholders})
        """)

        params = {f"id_{j}": mid for j, mid in enumerate(batch)}
        session.execute(query, params)
        updated += len(batch)

    session.commit()
    return updated


def main():
    # Import heavy dependencies only when running as main script
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from src.infra.adapters.cloudinary_image_store import CloudinaryImageStore
    from src.infra.database.config import SQLALCHEMY_DATABASE_URL

    parser = argparse.ArgumentParser(
        description="Find and mark orphaned meals (missing Cloudinary images)"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually update database (default is dry-run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of meals to check (for testing)",
    )
    args = parser.parse_args()

    logger.info("Scanning for orphaned meal images...")

    engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    cloudinary = CloudinaryImageStore()

    with SessionLocal() as session:
        orphans = find_orphaned_meals(session, cloudinary, limit=args.limit)

        if not orphans:
            logger.info("\nNo orphaned meals found!")
            return 0

        if args.execute:
            logger.info(f"\nMarking {len(orphans)} meals as FAILED...")
            updated = mark_meals_failed(session, orphans)
            logger.info(f"Updated {updated} meals to FAILED status")
        else:
            logger.info("\nDRY RUN - No changes made.")
            logger.info(
                f"Run with --execute to mark {len(orphans)} orphaned meals as FAILED."
            )
            logger.info("\nOrphaned meal IDs:")
            for mid in orphans[:20]:
                logger.info(f"  {mid}")
            if len(orphans) > 20:
                logger.info(f"  ... and {len(orphans) - 20} more")

    return 0


if __name__ == "__main__":
    sys.exit(main())
