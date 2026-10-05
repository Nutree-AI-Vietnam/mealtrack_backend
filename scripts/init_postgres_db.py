"""
PostgreSQL / Neon database initialisation script.

Use this instead of `alembic upgrade head` when setting up a fresh PostgreSQL
database. It:
  1. Creates required PostgreSQL extensions
  2. Creates all tables from the current SQLAlchemy models
  3. Stamps Alembic so future `alembic upgrade head` calls only run NEW migrations

Safe to run multiple times — it skips setup if tables already exist.

Usage:
    python scripts/init_postgres_db.py

Environment (highest to lowest priority):
    MIGRATION_DATABASE_URL  — preferred; direct Neon endpoint for migrations
    DATABASE_URL_DIRECT     — direct Neon endpoint (migration alias)
    DATABASE_URL            — fallback if neither above is set
"""

import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

# Override DATABASE_URL before importing the engine so it picks up the direct
# connection string (MIGRATION_DATABASE_URL) if provided.
migration_url = (
    os.getenv("MIGRATION_DATABASE_URL")
    or os.getenv("DATABASE_URL_DIRECT")
    or os.getenv("DATABASE_URL")
)
if not migration_url:
    print("ERROR: Set MIGRATION_DATABASE_URL, DATABASE_URL_DIRECT, or DATABASE_URL.")
    sys.exit(1)

# Normalise to psycopg2 driver for the sync engine used by this script.
_sync_url = migration_url
if _sync_url.startswith("postgres://"):
    _sync_url = _sync_url.replace("postgres://", "postgresql://", 1)
if "+asyncpg" in _sync_url:
    _sync_url = _sync_url.replace("+asyncpg", "", 1)
if "+psycopg2" not in _sync_url and _sync_url.startswith("postgresql://"):
    _sync_url = _sync_url.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(_sync_url, pool_pre_ping=True)

# Import every model so they register themselves on Base.metadata
import src.infra.database.models  # noqa: F401, E402
from src.domain.services.nutrition_integrity_policy import (  # noqa: E402
    NUTRITION_INTEGRITY_POLICY_VERSION,
)
from src.infra.database.base import Base  # noqa: E402


def db_is_fresh() -> bool:
    """Return True if no application tables exist yet."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    app_tables = [t for t in tables if t != "alembic_version"]
    return len(app_tables) == 0


def get_current_alembic_revision() -> str | None:
    """
    Return current Alembic revision, if present.

    A partially initialised database can contain application tables but no
    `alembic_version` table/row (e.g. interrupted bootstrap). In that state,
    running `alembic upgrade head` from base replays revision 001 and fails with
    duplicate-table errors. We detect that and recover by stamping head.
    """
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        return None

    with engine.begin() as conn:
        return conn.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        ).scalar()


def validate_schema_matches_metadata(inspector_override=None, metadata=None) -> None:
    """
    Guard recovery stamping against stale partial schemas.

    Stamping head is only safe when the database was already created from the
    current SQLAlchemy metadata and the Alembic row was the only missing piece.
    """
    inspector = inspector_override or inspect(engine)
    metadata = metadata or Base.metadata
    existing_tables = set(inspector.get_table_names())
    model_tables = sorted(metadata.tables)
    missing_tables = [table for table in model_tables if table not in existing_tables]
    missing_columns: list[str] = []

    for table_name in model_tables:
        if table_name not in existing_tables:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        for column in metadata.tables[table_name].columns:
            if column.name not in existing_columns:
                missing_columns.append(f"{table_name}.{column.name}")

    if not missing_tables and not missing_columns:
        return

    details = []
    if missing_tables:
        details.append("missing tables: " + ", ".join(missing_tables[:12]))
    if missing_columns:
        details.append("missing columns: " + ", ".join(missing_columns[:12]))

    raise RuntimeError(
        "Refusing to stamp Alembic head because the existing database schema "
        "does not match current SQLAlchemy metadata. Reset the local database "
        "or migrate it from its real revision first; " + "; ".join(details)
    )


def stamp_alembic_head(cfg: Config) -> None:
    """
    Persist Alembic head revision metadata directly in the database.

    Some local setups can end up with application tables but no `alembic_version`.
    Writing the version row ourselves makes bootstrap recovery deterministic.
    """
    head_revision = ScriptDirectory.from_config(cfg).get_current_head()
    if not head_revision:
        raise RuntimeError("Unable to resolve Alembic head revision.")

    with engine.begin() as conn:
        conn.execute(
            text("""
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL PRIMARY KEY
                )
                """)
        )
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": head_revision},
        )


def ensure_integrity_control_row(connection) -> None:
    """Keep the DB-owned integrity control singleton present after bootstrap."""
    if not inspect(connection).has_table("food_reference_integrity_control"):
        return

    connection.execute(
        text(
            """
            INSERT INTO food_reference_integrity_control
                (id, active_policy_version, catalog_integrity_generation, updated_at)
            VALUES (1, :policy, 0, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"policy": NUTRITION_INTEGRITY_POLICY_VERSION},
    )


def ensure_catalog_publication_control_row(connection) -> None:
    """Seed the catalog publication fence when metadata bootstrap stamps head."""
    if not inspect(connection).has_table("catalog_publication_version"):
        return

    connection.execute(
        text(
            """
            INSERT INTO catalog_publication_version (id)
            VALUES (1)
            ON CONFLICT (id) DO NOTHING
            """
        )
    )


def ensure_catalog_publication_triggers(connection) -> None:
    """Install migration-owned triggers when metadata bootstrap stamps head."""
    if connection.dialect.name != "postgresql":
        return

    from src.infra.database.catalog_publication_triggers import (
        SOURCE_TABLES,
        preparation_trigger_upgrade_sql,
        publication_trigger_upgrade_sql,
    )

    tables = set(inspect(connection).get_table_names())
    required_tables = {
        *SOURCE_TABLES,
        "catalog_publication_version",
        "meal_catalog_projection",
        "catalog_preparation_jobs",
    }
    if not required_tables.issubset(tables):
        return

    for statement in publication_trigger_upgrade_sql():
        if statement.startswith("CREATE FUNCTION"):
            statement = statement.replace(
                "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
            )
            connection.exec_driver_sql(statement)
            continue

        trigger_name = statement.split()[2]
        table_name = statement.split(" ON ", maxsplit=1)[1].split()[0]
        exists = connection.exec_driver_sql(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgrelid = to_regclass(%s) AND tgname = %s AND NOT tgisinternal
            )
            """,
            (table_name, trigger_name),
        ).scalar_one()
        if not exists:
            connection.exec_driver_sql(statement)

    for statement in preparation_trigger_upgrade_sql():
        connection.exec_driver_sql(statement)


def ensure_control_rows(connection) -> None:
    """Keep DB-owned singleton control rows present after any bootstrap path."""
    ensure_integrity_control_row(connection)
    ensure_catalog_publication_control_row(connection)
    ensure_catalog_publication_triggers(connection)


def main():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", migration_url)

    if not db_is_fresh():
        revision = get_current_alembic_revision()
        if revision is None:
            print(
                "Database has tables but no Alembic revision metadata; "
                "validating schema before recovery stamp."
            )
            validate_schema_matches_metadata()
            stamp_alembic_head(cfg)
            with engine.begin() as conn:
                ensure_control_rows(conn)
            print("Done.")
            return

        print(
            "Database already initialised — running alembic upgrade head for any new migrations."
        )
        command.upgrade(cfg, "head")
        with engine.begin() as conn:
            ensure_control_rows(conn)
        print("Done.")
        return

    print("Fresh database detected — building schema from models...")

    # 1. Enable required PostgreSQL extensions
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    print("  PostgreSQL extensions enabled.")

    # 2. Create all tables from current SQLAlchemy models
    Base.metadata.create_all(engine)
    print("  All tables created.")

    with engine.begin() as conn:
        ensure_control_rows(conn)

    # 3. Stamp Alembic at head so future migrations apply correctly
    stamp_alembic_head(cfg)
    print("  Alembic stamped at head.")

    print("Database initialisation complete.")


if __name__ == "__main__":
    main()
