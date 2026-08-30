"""
Shared utilities for database migrations.

Provides direct connection engine for migrations, bypassing PgBouncer pooler.
Neon's PgBouncer pooler doesn't handle DDL commits reliably.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool


def generate_timestamp_revision_id(
    versions_dir: Path | None = None,
    target_time: datetime | None = None,
) -> str:
    """Generate a 14-digit UTC timestamp revision ID: YYYYMMDDHHmmss.

    Example:
    - 20260829000002 (2026-08-29 00:00:02 UTC)
    """
    if target_time is None:
        target_time = datetime.now(UTC)

    base_id = target_time.strftime("%Y%m%d%H%M%S")
    if versions_dir is None or not versions_dir.exists():
        return base_id

    existing_revs = {
        f.name.split("_")[0]
        for f in versions_dir.glob("*.py")
        if not f.name.startswith("__")
    }

    if base_id not in existing_revs:
        return base_id

    # If same-second collision occurs, increment sequentially until unique
    candidate_val = int(base_id)
    while str(candidate_val) in existing_revs:
        candidate_val += 1
    return str(candidate_val)


# Backward compatibility alias
generate_sequential_revision_id = generate_timestamp_revision_id


def get_migration_url() -> str:
    """
    Get direct database URL for migrations, bypassing pooler.

    Priority:
    1. DATABASE_URL_DIRECT (explicit direct connection)
    2. DATABASE_URL with "-pooler" stripped (auto-convert)
    3. DATABASE_URL as-is

    Returns normalized URL for psycopg2.
    """
    direct_url = os.getenv("DATABASE_URL_DIRECT")
    base_url = os.getenv("DATABASE_URL", "")

    if direct_url:
        url = direct_url
    elif "-pooler" in base_url:
        url = base_url.replace("-pooler", "")
    else:
        url = base_url

    if not url:
        url = "postgresql+psycopg2://nutree:@localhost:5432/nutree"

    # Normalize protocol for psycopg2
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return url


def create_migration_engine() -> Engine:
    """
    Create dedicated engine for migrations with direct connection.

    Uses NullPool (no pooling) and keepalive settings for Neon stability.
    """
    return create_engine(
        get_migration_url(),
        echo=False,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": 10,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )


# Module-level singleton for imports
MIGRATION_URL = get_migration_url()
migration_engine = create_migration_engine()
