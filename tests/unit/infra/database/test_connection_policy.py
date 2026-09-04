"""Unit tests for DB connection policy resolution."""

import pytest
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from src.infra.database.connection_policy import (
    ConnectionPolicyError,
    _url_is_neon_pooler,
    resolve_connection_policy,
)


def test_url_is_neon_pooler_detects_pooler_host():
    assert _url_is_neon_pooler(
        "postgresql://user:pw@ep-xxx-pooler.us-east-1.aws.neon.tech/db"
    )


def test_url_is_neon_pooler_returns_false_for_direct():
    assert not _url_is_neon_pooler(
        "postgresql://user:pw@ep-xxx.us-east-1.aws.neon.tech/db"
    )


def test_app_database_url_takes_priority_over_database_url():
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@direct.neon.tech/db",
            "DATABASE_URL": "postgresql://user:pw@other.neon.tech/db",
        }
    )
    assert "direct.neon.tech" in policy.app_url


def test_database_url_used_when_no_app_database_url():
    policy = resolve_connection_policy(
        {
            "DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
        }
    )
    assert "ep-xxx.neon.tech" in policy.app_url


def test_database_url_direct_not_used_for_app_runtime():
    # DATABASE_URL_DIRECT must NOT affect app runtime URL selection
    policy = resolve_connection_policy(
        {
            "DATABASE_URL_DIRECT": "postgresql://user:pw@direct.neon.tech/db",
            "DATABASE_URL": "postgresql://user:pw@pooler.neon.tech/db",
        }
    )
    # Should use DATABASE_URL, not DATABASE_URL_DIRECT
    assert "pooler.neon.tech" in policy.app_url
    assert "direct.neon.tech" not in policy.app_url


def test_direct_pool_mode_selected_for_non_pooler_url():
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
            "DB_CONNECTION_MODE": "direct_pool",
        }
    )
    assert policy.mode == "direct_pool"
    assert policy.pool_class is AsyncAdaptedQueuePool


def test_neon_pooler_mode_uses_null_pool_and_prepared_statement_cache_zero():
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx-pooler.neon.tech/db",
            "DB_CONNECTION_MODE": "neon_pooler",
        }
    )
    assert policy.mode == "neon_pooler"
    assert policy.pool_class is NullPool
    assert policy.connect_args.get("prepared_statement_cache_size") == 0


def test_auto_detect_neon_pooler_from_url():
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx-pooler.neon.tech/db",
        }
    )
    assert policy.mode == "neon_pooler"


def test_auto_detect_direct_pool_from_non_pooler_url():
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
        }
    )
    assert policy.mode == "direct_pool"


def test_direct_pool_with_pooler_url_raises_error():
    with pytest.raises(ConnectionPolicyError, match="direct_pool"):
        resolve_connection_policy(
            {
                "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx-pooler.neon.tech/db",
                "DB_CONNECTION_MODE": "direct_pool",
            }
        )


def test_unknown_mode_raises_error():
    with pytest.raises(ConnectionPolicyError, match="Unknown DB_CONNECTION_MODE"):
        resolve_connection_policy(
            {
                "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
                "DB_CONNECTION_MODE": "invalid_mode",
            }
        )


def test_direct_pool_capacity_uses_workers_and_pool_size():
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
            "DB_CONNECTION_MODE": "direct_pool",
            "UVICORN_WORKERS": "2",
            "ASYNC_POOL_SIZE_PER_WORKER": "5",
            "ASYNC_POOL_MAX_OVERFLOW": "3",
        }
    )
    assert policy.pool_size == 5
    assert policy.max_overflow == 3
    assert policy.worker_count == 2
    assert policy.total_capacity == 16  # 2 workers * (5 base + 3 overflow)


def test_async_pool_size_per_worker_overrides_pool_size_per_worker():
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
            "DB_CONNECTION_MODE": "direct_pool",
            "UVICORN_WORKERS": "1",
            "POOL_SIZE_PER_WORKER": "2",
            "ASYNC_POOL_SIZE_PER_WORKER": "7",
        }
    )
    assert policy.pool_size == 7  # uses ASYNC_ variant
    assert policy.worker_count == 1


def test_migration_url_guard_is_not_app_runtime_url():
    """Guard: DATABASE_URL_DIRECT must not silently become the app URL."""
    env_only_direct = {
        "DATABASE_URL_DIRECT": "postgresql://user:pw@ep-xxx.neon.tech/db",
    }
    policy = resolve_connection_policy(env_only_direct)
    # Falls back to component URL, not DATABASE_URL_DIRECT
    assert "ep-xxx.neon.tech" not in policy.app_url


def test_neon_pooler_mode_with_direct_url_raises_error():
    """Guard: neon_pooler mode + non-pooler URL must raise, not silently misconfigure."""
    with pytest.raises(ConnectionPolicyError, match="neon_pooler"):
        resolve_connection_policy(
            {
                "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
                "DB_CONNECTION_MODE": "neon_pooler",
            }
        )


def test_async_pool_max_overflow_zero_is_respected():
    """ASYNC_POOL_MAX_OVERFLOW=0 must not fall through to default 2."""
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
            "DB_CONNECTION_MODE": "direct_pool",
            "ASYNC_POOL_MAX_OVERFLOW": "0",
        }
    )
    assert policy.max_overflow == 0


def test_async_db_use_queue_pool_env_var_is_silently_ignored():
    """ASYNC_DB_USE_QUEUE_POOL is a legacy no-op; mode is driven by URL + DB_CONNECTION_MODE."""
    policy = resolve_connection_policy(
        {
            "APP_DATABASE_URL": "postgresql://user:pw@ep-xxx.neon.tech/db",
            "ASYNC_DB_USE_QUEUE_POOL": "true",
        }
    )
    assert policy.mode == "direct_pool"
    assert policy.pool_class is AsyncAdaptedQueuePool
