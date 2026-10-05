"""Bounded, restartable job repair: python -m scripts.backfill_catalog_preparation."""

import argparse
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.bootstrap.inline_catalog_preparation import (
    preparation_locales,
    prepare_pending_catalog,
)
from src.infra.repositories.catalog_preparation_repository import (
    AsyncCatalogPreparationRepository,
)


async def run(args):
    from src.infra.workers.catalog_preparation_runtime import create_worker_engine

    if not 1 <= args.max_pages <= 1000:
        raise ValueError("Backfill max-pages must be between 1 and 1000")
    engine = create_worker_engine(capacity=1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cursor, total = args.after_id, 0
    try:
        for _ in range(args.max_pages):
            async with factory() as session:
                count, next_cursor = await AsyncCatalogPreparationRepository(
                    session
                ).backfill_page(
                    after_id=cursor,
                    limit=args.page_size,
                    locales=tuple(args.locales.split(",")),
                )
                await session.commit()
            total += count
            if next_cursor is None:
                break
            cursor = next_cursor
        print(f"preparation_jobs_inserted={total} next_cursor={cursor or ''}")
    finally:
        await engine.dispose()
    if not args.enqueue_only:
        print(f"catalog_prepared_jobs={await prepare_pending_catalog()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--after-id")
    parser.add_argument("--locales", default=",".join(preparation_locales()))
    parser.add_argument(
        "--enqueue-only",
        action="store_true",
        help="Queue jobs without translating/enriching them now",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
