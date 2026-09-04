"""Database connection mode resolution and pool configuration."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool


class ConnectionPolicyError(ValueError):
    """Raised on unsafe or contradictory env var combinations."""


@dataclass(frozen=True)
class DatabaseConnectionPolicy:
    """Immutable resolved DB connection settings for app runtime."""

    mode: str  # "direct_pool" | "neon_pooler"
    app_url: str  # raw (pre-asyncpg-normalization) URL selected for app runtime
    pool_class: type
    connect_args: dict = field(default_factory=dict)
    pool_size: int = 0
    max_overflow: int = 0
    pool_timeout: int = 10
    pool_recycle: int = 120
    worker_count: int = 1

    @property
    def total_capacity(self) -> int:
        """Total possible app DB connections across worker processes."""
        if self.pool_class is NullPool:
            return 0
        return self.worker_count * (self.pool_size + self.max_overflow)


def _int_env(env: dict, *keys: str, default: int) -> int:
    """Read the first key present (not None) as int; fall through to default."""
    for key in keys:
        val = env.get(key)
        if val is not None:
            return int(val)
    return default


def _url_is_neon_pooler(url: str) -> bool:
    """Return True if the host contains '-pooler' (Neon PgBouncer endpoint)."""
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        host = url
    return "-pooler" in host


def resolve_connection_policy(env: dict | None = None) -> DatabaseConnectionPolicy:
    """
    Resolve DB connection mode from environment variables.

    URL priority: APP_DATABASE_URL > DATABASE_URL > component fallback.
    DATABASE_URL_DIRECT is intentionally excluded from app-runtime URL selection
    — it is reserved for Alembic migration tooling only.
    """
    if env is None:
        env = dict(os.environ)

    raw_url = (
        env.get("APP_DATABASE_URL")
        or env.get("DATABASE_URL")
        or (
            "postgresql+asyncpg://{user}:{pw}@{host}:{port}/{db}".format(
                user=env.get("DB_USER", "nutree"),
                pw=env.get("DB_PASSWORD", ""),
                host=env.get("DB_HOST", "localhost"),
                port=env.get("DB_PORT", "5432"),
                db=env.get("DB_NAME", "nutree"),
            )
        )
    )

    explicit_mode = env.get("DB_CONNECTION_MODE", "").strip().lower()

    if not explicit_mode:
        mode = "neon_pooler" if _url_is_neon_pooler(raw_url) else "direct_pool"
    elif explicit_mode in ("direct_pool", "neon_pooler"):
        mode = explicit_mode
    else:
        raise ConnectionPolicyError(
            f"Unknown DB_CONNECTION_MODE={explicit_mode!r}. "
            "Expected 'direct_pool' or 'neon_pooler'."
        )

    if mode == "direct_pool" and _url_is_neon_pooler(raw_url):
        raise ConnectionPolicyError(
            "DB_CONNECTION_MODE=direct_pool requires a direct (non-pooler) Neon URL. "
            "The URL host contains '-pooler'. Set APP_DATABASE_URL to a direct "
            "Neon endpoint (no '-pooler' suffix in the hostname)."
        )

    if mode == "neon_pooler" and not _url_is_neon_pooler(raw_url):
        raise ConnectionPolicyError(
            "DB_CONNECTION_MODE=neon_pooler requires a Neon pooler URL "
            "('-pooler' in hostname). The provided URL is a direct endpoint. "
            "Either set APP_DATABASE_URL to a -pooler URL or use DB_CONNECTION_MODE=direct_pool."
        )

    workers = _int_env(env, "UVICORN_WORKERS", default=4)
    pool_size_per_worker = _int_env(
        env, "ASYNC_POOL_SIZE_PER_WORKER", "POOL_SIZE_PER_WORKER", default=3
    )
    max_overflow = _int_env(
        env, "ASYNC_POOL_MAX_OVERFLOW", "POOL_MAX_OVERFLOW", default=2
    )
    pool_timeout = _int_env(env, "ASYNC_POOL_TIMEOUT", "POOL_TIMEOUT", default=10)
    pool_recycle = _int_env(env, "ASYNC_POOL_RECYCLE", default=120)

    if mode == "neon_pooler":
        # NullPool: Neon PgBouncer manages connection reuse.
        # prepared_statement_cache_size=0 disables asyncpg caching, which is
        # required for PgBouncer transaction-mode compatibility.
        return DatabaseConnectionPolicy(
            mode="neon_pooler",
            app_url=raw_url,
            pool_class=NullPool,
            connect_args={"prepared_statement_cache_size": 0},
        )

    return DatabaseConnectionPolicy(
        mode="direct_pool",
        app_url=raw_url,
        pool_class=AsyncAdaptedQueuePool,
        pool_size=pool_size_per_worker,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        worker_count=workers,
    )
