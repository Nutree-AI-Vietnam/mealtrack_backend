"""Unit tests for async database config."""

import importlib

import pytest
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool


@pytest.fixture(autouse=True)
def _reset_config_async_module(monkeypatch):
    """Reload config_async after each test so module-level state never leaks."""
    monkeypatch.setenv("NEON_POOLER_USE_QUEUE_POOL", "false")
    yield
    import src.infra.database.config_async as cfg

    importlib.reload(cfg)


@pytest.mark.asyncio
async def test_get_async_db_raises_clear_error_when_session_factory_missing(
    monkeypatch,
):
    import src.infra.database.config_async as cfg

    monkeypatch.setattr(cfg, "AsyncSessionLocal", None)

    with pytest.raises(RuntimeError, match="AsyncSessionLocal is not initialized"):
        async for _ in cfg.get_async_db():
            pass


def test_async_url_rewrite_psycopg2_to_asyncpg(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql+psycopg2://user:pw@host/db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setenv("DB_CONNECTION_MODE", "")

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert "asyncpg" in cfg.ASYNC_DATABASE_URL
    assert "psycopg2" not in cfg.ASYNC_DATABASE_URL


def test_async_url_rewrite_plain_postgresql(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://user:pw@host/db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setenv("DB_CONNECTION_MODE", "")

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert cfg.ASYNC_DATABASE_URL.startswith("postgresql+asyncpg://")


def test_async_url_rewrite_postgres_shorthand(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "postgres://user:pw@host/db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setenv("DB_CONNECTION_MODE", "")

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert cfg.ASYNC_DATABASE_URL.startswith("postgresql+asyncpg://")


def test_app_database_url_takes_priority_over_database_url(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://app-host/appdb")
    monkeypatch.setenv("DATABASE_URL", "postgresql://other-host/otherdb")
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setenv("DB_CONNECTION_MODE", "")

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert "app-host" in cfg.ASYNC_DATABASE_URL


def test_database_url_direct_not_used_for_app_runtime(monkeypatch):
    """Guard: DATABASE_URL_DIRECT must not silently become the app runtime URL."""
    monkeypatch.setenv("DATABASE_URL_DIRECT", "postgresql://direct-host/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://app-host/db")
    # Use setenv("", ...) rather than delenv so load_dotenv() during reload
    # cannot re-add APP_DATABASE_URL from the .env file (override=False skips
    # vars already present in os.environ; "" is falsy in the or-chain).
    monkeypatch.setenv("APP_DATABASE_URL", "")
    monkeypatch.setenv("DB_CONNECTION_MODE", "")

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert "app-host" in cfg.ASYNC_DATABASE_URL
    assert "direct-host" not in cfg.ASYNC_DATABASE_URL


def test_async_engine_defaults_to_direct_pool(monkeypatch):
    """direct_pool (AsyncAdaptedQueuePool) is the default for non-pooler URLs."""
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://user:pw@host/db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setenv("DB_CONNECTION_MODE", "")

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert isinstance(cfg.async_engine.sync_engine.pool, AsyncAdaptedQueuePool)


def test_direct_pool_applies_pool_size_per_worker_to_engine(monkeypatch):
    """SQLAlchemy pool_size is per process; worker count is only for capacity reporting."""
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://user:pw@host/db")
    monkeypatch.setenv("DB_CONNECTION_MODE", "direct_pool")
    monkeypatch.setenv("UVICORN_WORKERS", "4")
    monkeypatch.setenv("ASYNC_POOL_SIZE_PER_WORKER", "8")
    monkeypatch.setenv("ASYNC_POOL_MAX_OVERFLOW", "3")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert cfg.async_engine.sync_engine.pool.size() == 8
    assert cfg._UVICORN_WORKERS == 4
    assert cfg._ASYNC_POOL_SIZE == 8
    assert cfg._ASYNC_POOL_OVERFLOW == 3
    assert cfg._ASYNC_POOL_TOTAL_CAPACITY == 44


def test_neon_pooler_url_auto_selects_null_pool(monkeypatch):
    """A -pooler URL automatically selects NullPool and pooler mode."""
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        "postgresql://user:pw@ep-xxx-pooler.us-east-1.aws.neon.tech/db",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setenv("DB_CONNECTION_MODE", "")

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert isinstance(cfg.async_engine.sync_engine.pool, NullPool)
    assert cfg.CONNECTION_MODE == "neon_pooler"


def test_explicit_neon_pooler_mode_uses_null_pool(monkeypatch):
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        "postgresql://user:pw@ep-xxx-pooler.us-east-1.aws.neon.tech/db",
    )
    monkeypatch.setenv("DB_CONNECTION_MODE", "neon_pooler")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert isinstance(cfg.async_engine.sync_engine.pool, NullPool)
    assert cfg._IS_NEON_POOLER is True


def test_neon_pooler_queue_pool_gets_pre_ping_and_recycle(monkeypatch):
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        "postgresql://user:pw@ep-xxx-pooler.us-east-1.aws.neon.tech/db",
    )
    monkeypatch.setenv("DB_CONNECTION_MODE", "neon_pooler")
    monkeypatch.setenv("NEON_POOLER_USE_QUEUE_POOL", "true")
    monkeypatch.setenv("ASYNC_POOL_SIZE_PER_WORKER", "7")
    monkeypatch.setenv("ASYNC_POOL_RECYCLE", "90")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    pool = cfg.async_engine.sync_engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool)
    assert pool._pre_ping is True
    assert pool._recycle == 90
    assert pool.size() == 7
    assert cfg.CONNECTION_MODE == "neon_pooler"


def test_connection_mode_exported(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://user:pw@host/db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL_DIRECT", raising=False)
    monkeypatch.setenv("DB_CONNECTION_MODE", "")

    import src.infra.database.config_async as cfg

    importlib.reload(cfg)
    assert cfg.CONNECTION_MODE in ("direct_pool", "neon_pooler")


@pytest.mark.parametrize("mode", ["direct_pool", "neon_queue", "neon_null"])
def test_engine_actual_pool_settings_and_driver_safety(monkeypatch, caplog, mode):
    neon = mode != "direct_pool"
    queue = mode != "neon_null"
    host = "ep-example-pooler.neon.tech" if neon else "ep-example.neon.tech"
    monkeypatch.setenv(
        "APP_DATABASE_URL",
        f"postgresql://user:pw@{host}/db?sslmode=require&channel_binding=require",
    )
    monkeypatch.setenv("DB_CONNECTION_MODE", "neon_pooler" if neon else "direct_pool")
    monkeypatch.setenv("NEON_POOLER_USE_QUEUE_POOL", str(mode == "neon_queue").lower())
    monkeypatch.setenv("UVICORN_WORKERS", "3")
    monkeypatch.setenv("ASYNC_POOL_SIZE_PER_WORKER", "7")
    monkeypatch.setenv("ASYNC_POOL_MAX_OVERFLOW", "2")
    monkeypatch.setenv("ASYNC_POOL_TIMEOUT", "17")
    monkeypatch.setenv("ASYNC_POOL_RECYCLE", "91")
    import src.infra.database.config_async as cfg

    with caplog.at_level("INFO"):
        importlib.reload(cfg)

    pool = cfg.async_engine.sync_engine.pool
    assert isinstance(pool, AsyncAdaptedQueuePool if queue else NullPool)
    if queue:
        assert pool.size() == 7
        assert pool._max_overflow == 2
        assert pool.timeout() == 17
        assert pool._recycle == 91
        assert pool._pre_ping is True
        assert cfg._ASYNC_POOL_TOTAL_CAPACITY == 27
    else:
        assert cfg._pool_kwargs == {}
        assert cfg._ASYNC_POOL_TOTAL_CAPACITY == 0

    # Intercept at the dialect connect boundary: these are the kwargs the
    # constructed engine will deliver to asyncpg, without opening a connection.
    driver_kwargs = {}
    sentinel = object()

    def capture_connect(*args, **kwargs):
        driver_kwargs.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        cfg.async_engine.sync_engine.dialect, "connect", capture_connect
    )
    assert pool._creator() is sentinel
    assert driver_kwargs["ssl"] is True
    assert "sslmode" not in driver_kwargs
    assert "channel_binding" not in driver_kwargs
    if neon:
        assert driver_kwargs["prepared_statement_cache_size"] == 0
    else:
        assert "prepared_statement_cache_size" not in driver_kwargs
    engine_logs = [
        r.message for r in caplog.records if r.message.startswith("Async engine:")
    ]
    assert any(
        f"Async engine: {type(pool).__name__} mode={cfg.CONNECTION_MODE}" in m
        for m in engine_logs
    )
    assert all("pw" not in m and host not in m for m in engine_logs)
