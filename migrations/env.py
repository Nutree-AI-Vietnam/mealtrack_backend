import os
import sys
from logging.config import fileConfig
from pathlib import Path

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from alembic import context
from migrations.utils import (
    MIGRATION_URL,
    generate_timestamp_revision_id,
    migration_engine,
)
from sqlalchemy import text

import src.infra.database.models  # noqa: F401
from src.infra.database.base import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def _migration_timeout_ms(env_name: str, default_ms: int) -> int:
    """Read a positive migration timeout in milliseconds."""
    raw = os.getenv(env_name)
    if raw is None:
        return default_ms
    try:
        value = int(raw)
        return max(value, 0)
    except ValueError:
        return default_ms


def _apply_migration_timeouts(connection) -> None:
    """Avoid silent deploy hangs when a migration waits on locks too long."""
    lock_timeout_ms = _migration_timeout_ms("MIGRATION_LOCK_TIMEOUT_MS", 10_000)
    statement_timeout_ms = _migration_timeout_ms(
        "MIGRATION_STATEMENT_TIMEOUT_MS",
        240_000,
    )

    connection.execute(text(f"SET lock_timeout = {lock_timeout_ms}"))
    connection.execute(text(f"SET statement_timeout = {statement_timeout_ms}"))
    # Commit SET statements to avoid transaction state issues
    connection.commit()


def _timestamp_rev_id(context, revision, directives):
    """Assign a 14-digit YYYYMMDDHHmmss revision ID (e.g. 20260829000002)."""
    if not directives:
        return
    versions_dir = Path(__file__).parent / "versions"
    directives[0].rev_id = generate_timestamp_revision_id(versions_dir)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = MIGRATION_URL or config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        process_revision_directives=_timestamp_rev_id,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Use direct connection engine for migrations (not pooler)
    connectable = migration_engine

    with connectable.connect() as connection:
        _apply_migration_timeouts(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            process_revision_directives=_timestamp_rev_id,
        )

        with context.begin_transaction():
            context.run_migrations()

        # Explicit commit required for Neon - transactional DDL auto-commit doesn't work
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
