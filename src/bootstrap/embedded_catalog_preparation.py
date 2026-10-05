"""Run catalog preparation inside the API process, next to request handling."""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.planner_feature_flags import (
    CATALOG_PREPARATION_IN_PROCESS,
    planner_flag_enabled,
)

logger = logging.getLogger(__name__)


@dataclass
class EmbeddedCatalogPreparation:
    stop_event: asyncio.Event
    task: asyncio.Task
    engine: Any
    client: httpx.AsyncClient

    async def stop(self, timeout_seconds: float = 10) -> None:
        self.stop_event.set()
        try:
            # On timeout the task is cancelled; its committed lease expires and
            # the job is reclaimed by the next process.
            await asyncio.wait_for(self.task, timeout_seconds)
        except (TimeoutError, asyncio.CancelledError):
            pass
        except Exception:
            logger.exception("catalog preparation stopped with an error")
        finally:
            await self.client.aclose()
            await self.engine.dispose()


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _log_unexpected_exit(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("catalog preparation loop exited", exc_info=task.exception())


async def start_embedded_catalog_preparation() -> EmbeddedCatalogPreparation | None:
    if not planner_flag_enabled(CATALOG_PREPARATION_IN_PROCESS):
        return None

    from src.bootstrap.catalog_preparation import build_preparation_computer
    from src.infra.workers.catalog_preparation_runtime import create_worker_engine
    from src.infra.workers.catalog_preparation_worker import (
        CatalogPreparationWorker,
    )

    concurrency = _env_int("CATALOG_PREPARATION_CONCURRENCY", 1)
    locales = tuple(
        locale.strip()
        for locale in os.getenv("CATALOG_PREPARATION_LOCALES", "vi,en").split(",")
        if locale.strip()
    )
    # A reserved pool keeps lease heartbeats from queueing behind API requests.
    engine = create_worker_engine(capacity=concurrency + 1)
    client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=concurrency, max_keepalive_connections=concurrency
        )
    )
    try:
        worker = CatalogPreparationWorker(
            async_sessionmaker(engine, expire_on_commit=False),
            build_preparation_computer(http_client=client),
            concurrency=concurrency,
            global_capacity=_env_int("CATALOG_PREPARATION_GLOBAL_CAPACITY", 4),
            locales=locales,
        )
    except Exception:
        await client.aclose()
        await engine.dispose()
        raise
    stop_event = asyncio.Event()
    task = asyncio.create_task(worker.run(stop_event), name="catalog-preparation")
    task.add_done_callback(_log_unexpected_exit)
    logger.info(
        "Catalog preparation running in-process: concurrency=%s locales=%s",
        concurrency,
        ",".join(locales),
    )
    return EmbeddedCatalogPreparation(stop_event, task, engine, client)
