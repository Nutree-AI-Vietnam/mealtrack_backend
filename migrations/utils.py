"""
Shared utilities for database migrations.

Provides direct connection engine for migrations, bypassing PgBouncer pooler.
Neon's PgBouncer pooler doesn't handle DDL commits reliably.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Ensure .env is loaded for migrations
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool


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
