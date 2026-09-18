"""Generate Cloudflare image URLs for imported catalog meals."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import selectinload

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.app.services.catalog_meal_image_prompt_service import (
    build_catalog_meal_image_prompt,
)
from src.api.base_dependencies import get_image_store
from src.infra.adapters.cloudflare_workers_image_generator import (
    CloudflareWorkersImageGenerator,
)
from src.infra.database.models.meal_recommendation import MealCatalogORM
from src.infra.database.uow_async import AsyncUnitOfWork


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(
        description="Generate post-import meal_catalog.image_url values via Cloudflare."
    )
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--catalog-key", action="append", default=[])
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--model",
        default=os.getenv(
            "CLOUDFLARE_WORKERS_AI_IMAGE_MODEL",
            "@cf/black-forest-labs/flux-2-klein-9b",
        ),
    )
    parser.add_argument("--quality", default="medium")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--output-format", default="jpeg")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    summary = asyncio.run(_run(args))
    print(f"selected={summary['selected']}")
    print(f"updated={summary['updated']}")
    print(f"skipped={summary['skipped']}")
    print(f"failed={summary['failed']}")
    if summary["failed"]:
        raise SystemExit(1)


async def _run(args) -> dict[str, int]:
    generator = None
    if not args.dry_run:
        generator = CloudflareWorkersImageGenerator(
            account_id=os.getenv("CLOUDFLARE_ACCOUNT_ID", ""),
            api_token=os.getenv("CLOUDFLARE_API_TOKEN", ""),
            model=args.model,
            timeout=args.timeout,
            image_store=get_image_store(),
        )
    selected = updated = skipped = failed = 0
    async with AsyncUnitOfWork() as uow:
        if uow.session is None:
            raise RuntimeError("AsyncUnitOfWork did not initialize a database session")
        meals = await _load_target_meals(
            uow.session,
            limit=args.limit,
            catalog_keys=tuple(args.catalog_key),
            include_existing=args.include_existing,
        )
    selected = len(meals)

    for meal in meals:
        prompt = build_catalog_meal_image_prompt(meal)
        print(f"meal={meal.catalog_key} name={meal.name}")
        if args.dry_run:
            continue
        try:
            if generator is None:
                raise RuntimeError("Cloudflare image generator is not initialized")
            image_url = await generator.generate_url(
                prompt,
                quality=args.quality,
                size=args.size,
                output_format=args.output_format,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(
                f"image_generation_failed catalog_key={meal.catalog_key} "
                f"error={_error_code(exc)}",
                file=sys.stderr,
            )
            continue
        try:
            persisted = await _persist_image_url(
                str(meal.id),
                image_url,
                include_existing=args.include_existing,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(
                f"image_persistence_failed catalog_key={meal.catalog_key} "
                f"error={_error_code(exc)}",
                file=sys.stderr,
            )
            continue
        if persisted:
            updated += 1
            print(f"image_generation_updated catalog_key={meal.catalog_key}")
        else:
            skipped += 1
            print(
                f"image_generation_skipped catalog_key={meal.catalog_key}: image_url already set",
                file=sys.stderr,
            )
    return {"selected": selected, "updated": updated, "skipped": skipped, "failed": failed}


async def _persist_image_url(
    catalog_id: str,
    image_url: str,
    *,
    include_existing: bool,
) -> bool:
    """Persist one generated URL without keeping a DB transaction open during I/O."""
    async with AsyncUnitOfWork() as uow:
        if uow.session is None:
            raise RuntimeError("AsyncUnitOfWork did not initialize a database session")
        return await _set_image_url(
            uow.session,
            catalog_id,
            image_url,
            include_existing=include_existing,
        )


def _error_code(exc: Exception) -> str:
    """Return an actionable error category without exposing provider details."""
    if "invalid signature" in str(exc).lower():
        return "cloudinary_signature_invalid"
    return type(exc).__name__


async def _load_target_meals(
    session,
    *,
    limit: int,
    catalog_keys: tuple[str, ...],
    include_existing: bool,
) -> list[MealCatalogORM]:
    stmt = (
        select(MealCatalogORM)
        .where(MealCatalogORM.is_active.is_(True))
        .options(selectinload(MealCatalogORM.ingredients))
        .order_by(MealCatalogORM.name)
        .limit(limit)
    )
    if catalog_keys:
        stmt = stmt.where(MealCatalogORM.catalog_key.in_(catalog_keys))
    if not include_existing:
        stmt = stmt.where(_missing_image_filter())
    result = await session.execute(stmt)
    return list(result.scalars().unique().all())


async def _set_image_url(
    session,
    catalog_id: str,
    image_url: str,
    *,
    include_existing: bool,
) -> bool:
    stmt = update(MealCatalogORM).where(MealCatalogORM.id == catalog_id)
    if not include_existing:
        stmt = stmt.where(_missing_image_filter())
    result = await session.execute(stmt.values(image_url=image_url))
    await session.flush()
    return bool(result.rowcount)


def _missing_image_filter():
    return or_(MealCatalogORM.image_url.is_(None), func.trim(MealCatalogORM.image_url) == "")


if __name__ == "__main__":
    main()
