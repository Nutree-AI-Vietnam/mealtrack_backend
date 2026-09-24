"""Async database engine, session factory, and FastAPI dependency."""

import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.infra.database.connection_policy import (
    DatabaseConnectionPolicy,
    resolve_connection_policy,
)

load_dotenv()

logger = logging.getLogger(__name__)


def _sanitize_asyncpg_url_and_connect_args(url: str) -> tuple[str, dict]:
    """
    Remove libpq-only URL params that asyncpg cannot accept as connect kwargs.

    SQLAlchemy's URL query params are forwarded as driver kwargs, so we strip
    unsupported options and translate compatible ones into asyncpg connect_args.
    """
    try:
        parts = urlsplit(url)
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)
        sslmode: str | None = None
        filtered: list[tuple[str, str]] = []
        connect_args: dict = {}
        for k, v in query_pairs:
            if k == "sslmode":
                sslmode = v
                continue
            if k == "channel_binding":
                continue
            filtered.append((k, v))

        if sslmode and sslmode.lower() not in {"disable", "allow", "prefer"}:
            connect_args["ssl"] = True

        if sslmode is None and len(filtered) == len(query_pairs):
            return url, connect_args

        new_query = urlencode(filtered)
        sanitized = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )
        return sanitized, connect_args
    except Exception:  # noqa: BLE001
        return url, {}


def _normalize_asyncpg_url(raw_url: str) -> str:
    """Ensure the URL uses the postgresql+asyncpg:// driver prefix."""
    if raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw_url.startswith("postgresql://") and "+asyncpg" not in raw_url:
        return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw_url.startswith("postgresql+psycopg2://"):
        return raw_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return raw_url


# Resolve connection policy from environment.
# URL priority: APP_DATABASE_URL > DATABASE_URL > component fallback.
# DATABASE_URL_DIRECT is reserved for migration tooling; it is NOT used here.
_policy: DatabaseConnectionPolicy = resolve_connection_policy()

# Normalize driver and sanitize asyncpg URL params
ASYNC_DATABASE_URL = _normalize_asyncpg_url(_policy.app_url)
ASYNC_DATABASE_URL, _url_connect_args = _sanitize_asyncpg_url_and_connect_args(
    ASYNC_DATABASE_URL
)

# Merge connect_args: url-level (ssl from sslmode) first, then policy-level
# (prepared_statement_cache_size=0 for pooler) so policy settings take precedence.
_connect_args = {**_url_connect_args, **_policy.connect_args}

# Expose connection mode for health endpoint and observability
CONNECTION_MODE = _policy.mode
_IS_NEON_POOLER = _policy.mode == "neon_pooler"  # backward-compat alias

_UVICORN_WORKERS = _policy.worker_count
_ASYNC_POOL_SIZE = _policy.pool_size
_ASYNC_POOL_OVERFLOW = _policy.max_overflow
_ASYNC_POOL_TOTAL_CAPACITY = _policy.total_capacity

try:
    # Engine creation branches on the resolved pool class, not the mode name, because
    # neon_pooler can run with either NullPool (the default) or AsyncAdaptedQueuePool
    # (when NEON_POOLER_USE_QUEUE_POOL=true).
    #
    # Why have a local queue pool in front of Neon's PgBouncer?
    # ---------------------------------------------------------
    # Neon's PgBouncer endpoint operates in transaction mode: it provides external
    # connection pooling so the database server is not overwhelmed.  However, each
    # time the app checks out a connection from SQLAlchemy's NullPool it must perform
    # a fresh TLS handshake to PgBouncer.  Under high concurrency (many Uvicorn
    # workers firing simultaneous requests) that per-checkout overhead adds up.
    #
    # With NEON_POOLER_USE_QUEUE_POOL=true the app keeps a small local
    # AsyncAdaptedQueuePool of persistent TCP/TLS connections to PgBouncer.
    # Requests are served from those already-open connections, eliminating the
    # per-request handshake cost.  PgBouncer still multiplexes those connections
    # onto a much smaller set of actual Postgres server connections, so the database
    # side is still protected.
    #
    # When a local queue pool is active it must be configured with pool_pre_ping,
    # pool_recycle, and pool_timeout so that connections closed by PgBouncer or
    # Postgres (idle timeout) are detected and replaced before being handed to a
    # request — without these settings a stale connection causes an immediate
    # "connection is closed" InterfaceError.
    if _policy.pool_class is NullPool:
        # NullPool: every request opens and closes its own connection to PgBouncer.
        # No local pool arguments are valid here; PgBouncer handles reuse.
        async_engine = create_async_engine(
            ASYNC_DATABASE_URL,
            echo=False,
            poolclass=_policy.pool_class,
            connect_args=_connect_args,
        )
        logger.info(
            "Async engine: NullPool mode=%s (PgBouncer manages connection reuse)",
            _policy.mode,
        )
    else:
        # AsyncAdaptedQueuePool: used for direct_pool mode and for neon_pooler with
        # NEON_POOLER_USE_QUEUE_POOL=true (local pool in front of PgBouncer).
        # pool_pre_ping validates connections before use so closed/stale ones are
        # never returned to a request.
        async_engine = create_async_engine(
            ASYNC_DATABASE_URL,
            echo=False,
            poolclass=_policy.pool_class,
            pool_size=_policy.pool_size,
            max_overflow=_policy.max_overflow,
            pool_recycle=_policy.pool_recycle,
            pool_timeout=_policy.pool_timeout,
            pool_pre_ping=True,
            connect_args=_connect_args,
        )
        logger.info(
            "Async engine: AsyncAdaptedQueuePool mode=%s pool_size=%s max_overflow=%s",
            _policy.mode,
            _policy.pool_size,
            _policy.max_overflow,
        )

    AsyncSessionLocal = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
except Exception as _engine_init_error:  # noqa: BLE001
    logger.warning(
        "Async engine could not be initialised at import time (%s); "
        "any attempt to open an AsyncUnitOfWork will raise.",
        _engine_init_error,
    )
    async_engine = None  # type: ignore[assignment]
    AsyncSessionLocal = None  # type: ignore[assignment]


async def get_async_db():
    """FastAPI dependency: yields an AsyncSession, closes after request."""
    if AsyncSessionLocal is None:
        raise RuntimeError(
            "AsyncSessionLocal is not initialized. Async engine setup failed; "
            "check async DB configuration."
        )

    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
