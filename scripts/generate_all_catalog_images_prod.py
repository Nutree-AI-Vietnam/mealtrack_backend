"""Concurrent batch image generation for catalog meals using Cloudflare Workers AI and Cloudflare Images."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.api.base_dependencies import get_image_store
from src.app.services.catalog_meal_image_prompt_service import (
    build_catalog_meal_image_prompt,
)
from src.infra.adapters.cloudflare_workers_image_generator import (
    CloudflareWorkersImageGenerator,
)

DEFAULT_PROD_URL = "postgresql://neondb_owner:npg_Snwt8Af9mWrg@ep-silent-bar-aklthiqy.c-3.us-west-2.aws.neon.tech/neondb?sslmode=require"


class SimpleMeal:
    def __init__(self, name: str, cuisine: str, ingredients: list[str]):
        self.name = name
        self.cuisine = cuisine
        self.ingredients = [SimpleIngredient(ing) for ing in ingredients]


class SimpleIngredient:
    def __init__(self, display_name: str):
        self.display_name = display_name


async def fetch_target_meals(
    conn: asyncpg.Connection,
    limit: int | None = None,
) -> list[dict]:
    limit_clause = f"LIMIT {limit}" if limit and limit > 0 else ""
    rows = await conn.fetch(
        f"""
        SELECT m.id, m.catalog_key, m.name, m.cuisine,
               array_remove(array_agg(i.display_name ORDER BY i.position), NULL) as ingredients
        FROM meal_catalog m
        LEFT JOIN meal_catalog_ingredients i ON i.catalog_meal_id = m.id
        WHERE m.is_active = true AND (m.image_url IS NULL OR trim(m.image_url) = '')
        GROUP BY m.id, m.catalog_key, m.name, m.cuisine
        ORDER BY m.name
        {limit_clause}
        """
    )
    return [dict(r) for r in rows]


async def update_meal_image(
    conn: asyncpg.Connection,
    meal_id: str,
    image_url: str,
) -> bool:
    res = await conn.execute(
        """
        UPDATE meal_catalog
        SET image_url = $1, updated_at = NOW()
        WHERE id = $2 AND (image_url IS NULL OR trim(image_url) = '')
        """,
        image_url,
        meal_id,
    )
    return res == "UPDATE 1"


async def process_meal(
    meal: dict,
    idx: int,
    total: int,
    generator: CloudflareWorkersImageGenerator,
    pool: asyncpg.Pool,
    sem: asyncio.Semaphore,
    stats: dict,
    start_time: float,
    max_retries: int = 3,
) -> None:
    async with sem:
        catalog_key = meal["catalog_key"]
        name = meal["name"]
        cuisine = meal["cuisine"] or "vietnamese"
        ingredients = meal.get("ingredients") or []

        dummy_meal = SimpleMeal(name, cuisine, ingredients)
        prompt = build_catalog_meal_image_prompt(dummy_meal)
        image_id = f"catalog_meals/{catalog_key}"

        t0 = time.time()
        image_url = None
        for attempt in range(1, max_retries + 1):
            try:
                image_url = await generator.generate_url(
                    prompt,
                    image_id=image_id,
                    quality="medium",
                    size="1024x1024",
                    output_format="jpeg",
                )
                break
            except Exception as exc:
                if attempt < max_retries:
                    backoff = attempt * 3.0
                    print(
                        f"[{idx}/{total}] Attempt {attempt} failed for {catalog_key}: {exc}. Retrying in {backoff:.1f}s...",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(backoff)
                else:
                    print(
                        f"[{idx}/{total}] FAILED {catalog_key} after {max_retries} attempts: {exc}",
                        file=sys.stderr,
                    )
                    stats["failed"] += 1
                    return

        if not image_url:
            stats["failed"] += 1
            return

        async with pool.acquire() as conn:
            persisted = await update_meal_image(conn, meal["id"], image_url)

        dt = time.time() - t0
        elapsed = time.time() - start_time
        completed = stats["updated"] + stats["skipped"] + stats["failed"] + 1
        rate = completed / elapsed if elapsed > 0 else 0
        rem_seconds = (total - completed) / rate if rate > 0 else 0
        rem_min = int(rem_seconds // 60)
        rem_sec = int(rem_seconds % 60)

        if persisted:
            stats["updated"] += 1
            print(
                f"[{completed}/{total}] ({completed/total*100:.1f}%) "
                f"UPDATED {catalog_key} in {dt:.1f}s | "
                f"ETA: {rem_min}m{rem_sec:02d}s | URL: {image_url}"
            )
        else:
            stats["skipped"] += 1
            print(
                f"[{completed}/{total}] SKIPPED {catalog_key} (already set) in {dt:.1f}s"
            )


async def run_batch(args: argparse.Namespace) -> None:
    import ssl
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    load_dotenv(".env")
    raw_url = args.db_url or os.getenv("PROD_DATABASE_URL") or DEFAULT_PROD_URL

    parts = urlsplit(raw_url)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    filtered = [(k, v) for k, v in query_pairs if k not in ("sslmode", "channel_binding")]
    clean_query = urlencode(filtered)
    clean_dsn = urlunsplit((parts.scheme, parts.netloc, parts.path, clean_query, parts.fragment))

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    print(f"Connecting to database...")
    pool = await asyncpg.create_pool(
        clean_dsn,
        ssl=ssl_ctx,
        min_size=2,
        max_size=args.concurrency + 2,
        timeout=30.0,
    )
    assert pool is not None

    async with pool.acquire() as conn:
        meals = await fetch_target_meals(conn, limit=args.limit)

    total = len(meals)
    print(f"Found {total} active catalog meals needing images.")
    if total == 0:
        await pool.close()
        return

    generator = CloudflareWorkersImageGenerator(
        account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""),
        api_token=os.getenv("CLOUDFLARE_API_TOKEN", ""),
        model=args.model,
        timeout=args.timeout,
        image_store=get_image_store(),
    )

    sem = asyncio.Semaphore(args.concurrency)
    stats = {"updated": 0, "skipped": 0, "failed": 0}
    start_time = time.time()

    print(
        f"Starting generation with concurrency={args.concurrency}, model={args.model}..."
    )

    tasks = [
        process_meal(
            meal=meal,
            idx=i + 1,
            total=total,
            generator=generator,
            pool=pool,
            sem=sem,
            stats=stats,
            start_time=start_time,
            max_retries=args.max_retries,
        )
        for i, meal in enumerate(meals)
    ]

    await asyncio.gather(*tasks)

    total_time = time.time() - start_time
    print("=" * 60)
    print("BATCH IMAGE GENERATION COMPLETE")
    print(f"Total meals selected: {total}")
    print(f"Successfully updated: {stats['updated']}")
    print(f"Skipped:              {stats['skipped']}")
    print(f"Failed:               {stats['failed']}")
    print(f"Total time elapsed:   {int(total_time // 60)}m {int(total_time % 60)}s")
    print("=" * 60)

    await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch generate meal catalog images concurrently."
    )
    parser.add_argument("--db-url", default=None, help="Database connection URL")
    parser.add_argument(
        "--concurrency", type=int, default=3, help="Concurrent workers (default: 3)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max meals to generate (0 for all missing)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv(
            "CLOUDFLARE_WORKERS_AI_IMAGE_MODEL",
            "@cf/black-forest-labs/flux-2-klein-9b",
        ),
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-retries", type=int, default=3)

    args = parser.parse_args()
    asyncio.run(run_batch(args))


if __name__ == "__main__":
    main()
