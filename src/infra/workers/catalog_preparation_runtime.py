"""Reserved worker database pool, preserving asyncpg/PgBouncer policy."""

import os

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from src.infra.database.connection_policy import resolve_connection_policy


def create_worker_engine(*, capacity):
    from src.infra.database.config_async import (
        _normalize_asyncpg_url,
        _sanitize_asyncpg_url_and_connect_args,
    )

    env = dict(os.environ)
    if env.get("CATALOG_WORKER_DATABASE_URL"):
        env["APP_DATABASE_URL"] = env["CATALOG_WORKER_DATABASE_URL"]
        env["DB_CONNECTION_MODE"] = env.get("CATALOG_WORKER_DB_CONNECTION_MODE", "")
    env.update(
        UVICORN_WORKERS="1",
        ASYNC_POOL_SIZE_PER_WORKER=str(capacity),
        ASYNC_POOL_MAX_OVERFLOW="0",
        ASYNC_POOL_TIMEOUT="5",
        NEON_POOLER_USE_QUEUE_POOL="true",
    )
    policy = resolve_connection_policy(env)
    url, url_args = _sanitize_asyncpg_url_and_connect_args(
        _normalize_asyncpg_url(policy.app_url)
    )
    return create_async_engine(
        url,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=capacity,
        max_overflow=0,
        pool_timeout=5,
        pool_recycle=policy.pool_recycle,
        pool_pre_ping=True,
        connect_args={**url_args, **policy.connect_args},
    )
