"""Prepare catalog text and micronutrients right after recipes are written."""

import asyncio
import logging
import os

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


def preparation_locales() -> tuple[str, ...]:
    raw = os.getenv("CATALOG_PREPARATION_LOCALES", "vi,en")
    return tuple(locale.strip() for locale in raw.split(",") if locale.strip())


async def prepare_pending_catalog(
    *,
    concurrency: int = 2,
    max_jobs: int = 5000,
    max_consecutive_errors: int = 5,
    retry_delay_seconds: float = 2.0,
) -> int:
    """Run every claimable preparation job to completion, then return.

    Jobs that fail are left in retry_wait with a future availability time, so
    the loop always ends; the next import or backfill picks them up again.
    Infrastructure errors (e.g. a dropped database connection) are retried
    with backoff; a lane stops after ``max_consecutive_errors`` in a row.
    """
    from src.bootstrap.catalog_preparation import build_preparation_computer
    from src.infra.config.settings import settings
    from src.infra.workers.catalog_preparation_runtime import create_worker_engine
    from src.infra.workers.catalog_preparation_worker import (
        CatalogPreparationWorker,
    )

    if not settings.OPENAI_API_KEY:
        # Without a provider, translation jobs fail permanently and are not
        # re-queued, so leave them pending for a run that has the key.
        logger.warning("OPENAI_API_KEY is not set; catalog preparation skipped")
        return 0

    engine = create_worker_engine(capacity=concurrency + 1)
    processed = 0
    try:
        async with httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=concurrency, max_keepalive_connections=concurrency
            )
        ) as client:
            worker = CatalogPreparationWorker(
                async_sessionmaker(engine, expire_on_commit=False),
                build_preparation_computer(http_client=client),
                concurrency=concurrency,
                global_capacity=max(4, concurrency),
                locales=preparation_locales(),
            )

            async def lane() -> None:
                nonlocal processed
                errors = 0
                while processed < max_jobs:
                    # Reserve the slot before awaiting so concurrent lanes
                    # cannot all pass the check and overshoot max_jobs.
                    processed += 1
                    try:
                        worked = await worker.run_once()
                    except Exception:
                        processed -= 1
                        errors += 1
                        logger.exception(
                            "catalog preparation job failed (%s/%s in a row)",
                            errors,
                            max_consecutive_errors,
                        )
                        if errors >= max_consecutive_errors:
                            return
                        await asyncio.sleep(retry_delay_seconds * errors)
                        continue
                    errors = 0
                    if not worked:
                        processed -= 1
                        return

            await asyncio.gather(*(lane() for _ in range(concurrency)))
    finally:
        await engine.dispose()
    logger.info("Catalog preparation processed %s jobs", processed)
    return processed
