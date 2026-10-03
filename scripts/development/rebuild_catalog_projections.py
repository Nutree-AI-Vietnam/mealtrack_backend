"""Explicit, bounded catalog backfill against a caller-selected database.

Set CATALOG_PROJECTION_DATABASE_URL, then invoke with --batch-size and
--max-batches. Resume from the printed after_id only after a committed batch.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import src.infra.database.models  # noqa: F401, E402
from src.infra.repositories.catalog_projection_rebuilder import (
    CatalogProjectionRebuilder,  # noqa: E402
)


async def run(*, batch_size: int, max_batches: int, after_id: str | None):
    url = os.environ.get("CATALOG_PROJECTION_DATABASE_URL")
    if not url:
        raise SystemExit(
            "Set CATALOG_PROJECTION_DATABASE_URL to select the backfill database"
        )
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    total = 0
    try:
        for _ in range(max_batches):
            async with session_factory.begin() as session:
                count, cursor = await CatalogProjectionRebuilder(
                    session
                ).reconcile_page(after_id=after_id, limit=batch_size)
            total += count
            if cursor is None:
                print(f"complete rebuilt={total}")
                return
            after_id = cursor
            print(f"committed rebuilt={count} after_id={after_id}")
        print(f"paused rebuilt={total} after_id={after_id}")
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=1)
    parser.add_argument("--after-id")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 500 or args.max_batches < 1:
        parser.error("batch-size must be 1..500 and max-batches must be positive")
    asyncio.run(
        run(
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            after_id=args.after_id,
        )
    )


if __name__ == "__main__":
    main()
