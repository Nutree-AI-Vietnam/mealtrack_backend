"""Async database engine, session factory, and FastAPI dependency."""

import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from src.infra.database.connection_policy import (
    DatabaseConnectionPolicy,
    resolve_connection_policy,
)
from src.infra.database.planner_pool import PlannerNullPool, PlannerQueuePool

load_dotenv()

logger = logging.getLogger(__name__)

# Bound so a hung pool terminate cannot hold the request open. The close task
# itself keeps running after this wait returns.
SESSION_CLOSE_TIMEOUT_SECONDS = 5.0

# Strong refs for session-close tasks. The loop only keeps tasks weakly, so a
# CancelledError unwinding the request can destroy a close that is still
# terminating the pooled connection.
_CLOSING_SESSIONS: set[asyncio.Task[None]] = set()
_CLOSE_WAITS: dict[asyncio.Task[None], "_CloseWait"] = {}


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
    # Neon mode can use either NullPool or a bounded local queue pool. Only
    # queue pools accept capacity settings; select kwargs by class, not mode.
    _pool_kwargs = {}
    if issubclass(_policy.pool_class, AsyncAdaptedQueuePool):
        _pool_kwargs = {
            "pool_size": _policy.pool_size,
            "max_overflow": _policy.max_overflow,
            "pool_recycle": _policy.pool_recycle,
            "pool_timeout": _policy.pool_timeout,
            "pool_pre_ping": True,
        }
    async_engine = create_async_engine(
        ASYNC_DATABASE_URL,
        echo=False,
        poolclass=PlannerQueuePool if _pool_kwargs else PlannerNullPool,
        connect_args=_connect_args,
        **_pool_kwargs,
    )
    _actual_pool_class = type(async_engine.sync_engine.pool).__name__
    if _pool_kwargs:
        logger.info(
            "Async engine: %s mode=%s pool_size=%s max_overflow=%s "
            "pool_timeout=%s pool_recycle=%s pre_ping=true workers=%s capacity=%s",
            _actual_pool_class,
            _policy.mode,
            _policy.pool_size,
            _policy.max_overflow,
            _policy.pool_timeout,
            _policy.pool_recycle,
            _policy.worker_count,
            _policy.total_capacity,
        )
    else:
        logger.info(
            "Async engine: %s mode=%s (PgBouncer manages connection reuse)",
            _actual_pool_class,
            _policy.mode,
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

    session = AsyncSessionLocal()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await close_async_session(session)


class _CloseWait:
    """Whether the caller is still waiting to observe a close failure."""

    __slots__ = ("attached",)

    def __init__(self) -> None:
        self.attached = True


def _on_close_done(task: asyncio.Task[None]) -> None:
    _CLOSING_SESSIONS.discard(task)
    wait = _CLOSE_WAITS.pop(task, None)
    if task.cancelled():
        return
    error = task.exception()
    if error is None or (wait is not None and wait.attached):
        # The waiter raises this failure itself. Logging here too reports it
        # twice on a normal request.
        return
    logger.error("Async DB session close failed", exc_info=error)


def _caller_was_cancelled() -> bool:
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0


async def close_async_session(session: AsyncSession) -> None:
    """Close a session even when the request task is already cancelled.

    Chat does not use ``get_async_db``. Its sessions are closed from
    ``AsyncUnitOfWork.__aexit__`` and from the auth lookup's short-lived
    session. A client disconnect cancels that task while ``NullPool`` is
    terminating the connection, which logs "Exception terminating connection"
    and drops the cleanup (PYTHON-HP).

    The close runs on a shielded task with a strong reference. The wait is
    bounded so a hung close cannot hold the request. ``CancelledError`` is
    re-raised when the caller was cancelled.
    """
    close_task = asyncio.create_task(session.close())
    wait = _CloseWait()
    _CLOSING_SESSIONS.add(close_task)
    _CLOSE_WAITS[close_task] = wait
    close_task.add_done_callback(_on_close_done)
    try:
        await asyncio.wait_for(
            asyncio.shield(close_task),
            SESSION_CLOSE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Async DB session close exceeded %ss; releasing the request",
            SESSION_CLOSE_TIMEOUT_SECONDS,
        )
        wait.attached = False
        if _caller_was_cancelled():
            raise asyncio.CancelledError() from None
    except asyncio.CancelledError:
        wait.attached = False
        raise
