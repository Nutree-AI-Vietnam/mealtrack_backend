#!/usr/bin/env python
"""
Database migration runner with retry logic and proper error handling.

This script runs Alembic migrations with:
- Database connection retry with exponential backoff
- Proper first-time initialization
- Detailed logging
- Clean error handling
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from migrations.utils import migration_engine as engine
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError, OperationalError

# Configure logging with timestamp
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Configuration
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 2.0  # seconds
MAX_RETRY_DELAY = 30.0  # seconds
ALEMBIC_CONFIG_PATH = "alembic.ini"


def wait_for_database(max_retries: int = MAX_RETRIES) -> bool:
    """
    Wait for database to become available with exponential backoff.

    Args:
        max_retries: Maximum number of connection attempts

    Returns:
        bool: True if connection successful, False otherwise
    """
    delay = INITIAL_RETRY_DELAY

    for attempt in range(1, max_retries + 1):
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                result.fetchone()  # Ensure query completes
                conn.commit()  # Explicitly commit
            logger.info("✅ Database connection established")
            return True

        except (OperationalError, DatabaseError) as e:
            if attempt == max_retries:
                logger.error(f"❌ Failed to connect after {max_retries} attempts: {e}")
                return False

            logger.warning(
                f"Database not ready (attempt {attempt}/{max_retries}), retrying in {delay}s..."
            )
            time.sleep(delay)
            delay = min(delay * 2, MAX_RETRY_DELAY)  # Exponential backoff with cap

    return False


def get_alembic_config() -> Config | None:
    """
    Load and validate Alembic configuration.

    Returns:
        Config object if successful, None otherwise
    """
    config_path = Path(ALEMBIC_CONFIG_PATH)

    if not config_path.exists():
        logger.error(f"❌ Alembic config not found: {config_path.absolute()}")
        return None

    try:
        alembic_cfg = Config(str(config_path))
        # Ensure database URL is set from engine
        alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
        return alembic_cfg

    except Exception as e:
        logger.error(f"❌ Failed to load Alembic config: {e}")
        return None


def initialize_first_deployment(alembic_cfg: Config) -> bool:
    """
    Validate that first deployment can run through Alembic from base.

    Args:
        alembic_cfg: Alembic configuration object

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        inspector = inspect(engine)
        tables = inspector.get_table_names()

        logger.info(f"📊 Found {len(tables)} existing tables")

        # Check if we have any application tables (excluding alembic_version)
        app_tables = [t for t in tables if t != "alembic_version"]

        if app_tables:
            logger.error(
                "Existing application tables found without alembic_version: %s. "
                "Refusing to stamp or create schema automatically; repair this "
                "database with an explicit Alembic baseline plan.",
                ", ".join(app_tables[:10]),
            )
            return False

        script_dir = ScriptDirectory.from_config(alembic_cfg)
        head_revision = script_dir.get_current_head()
        logger.info(
            "Empty database detected; Alembic will upgrade from base to head=%s",
            head_revision or "<none>",
        )

        return True

    except Exception as e:
        logger.error(f"❌ First deployment initialization failed: {e}", exc_info=True)
        return False


def run_migrations(
    action: str = "upgrade",
    target: str = "head",
) -> bool:
    """Run database migrations or rollbacks with proper error handling.

    Args:
        action: 'upgrade' or 'downgrade'/'rollback'
        target: Target revision ('head', '-1', or revision ID)

    Returns:
        bool: True if successful, False otherwise
    """
    logger.info(
        f"🚀 Starting database migration process (action={action}, target={target})..."
    )

    # Step 1: Wait for database
    if not wait_for_database():
        logger.error("❌ Cannot proceed without database connection")
        return False

    # Step 2: Load Alembic config
    alembic_cfg = get_alembic_config()
    if not alembic_cfg:
        return False

    try:
        # Step 3: Check current state
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        script_dir = ScriptDirectory.from_config(alembic_cfg)
        head_revision = script_dir.get_current_head()
        current_revision = None

        if "alembic_version" in tables:
            with engine.connect() as conn:
                current_revision = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()

        logger.info(
            "📌 Alembic revision before run: current=%s head=%s",
            current_revision or "<none>",
            head_revision or "<none>",
        )

        # Check for orphan revision (database ahead of codebase)
        if current_revision is not None:
            all_revisions = {rev.revision for rev in script_dir.walk_revisions()}
            if current_revision not in all_revisions:
                logger.error(
                    "\n"
                    "===============================================================\n"
                    "❌ ORPHAN REVISION / DATABASE AHEAD OF CODEBASE DETECTED!\n"
                    "===============================================================\n"
                    f"The database has revision '{current_revision}', which DOES NOT EXIST\n"
                    f"in this codebase's migration history (head={head_revision}).\n\n"
                    "This typically happens when a Git commit was reverted or an older\n"
                    "container image was deployed without first rolling back the database.\n\n"
                    "HOW TO RESOLVE:\n"
                    "  1. Pre-Revert Rollback:\n"
                    "     Run the migration runner/image from the branch that introduced\n"
                    f"     '{current_revision}' and execute a rollback to {head_revision}.\n\n"
                    "  2. Forward-Fix Reversion:\n"
                    "     Keep the migration file in git and create a new forward migration\n"
                    "     to revert schema changes rather than deleting the migration file.\n"
                    "==============================================================="
                )
                return False

        # Step 4: Handle first deployment (only during forward upgrade)
        if action == "upgrade":
            if "alembic_version" not in tables:
                logger.info("🆕 First deployment detected, initializing...")
                if not initialize_first_deployment(alembic_cfg):
                    return False
            else:
                logger.info("📋 Existing deployment detected")

            # Step 5: Run upgrade
            logger.info(f"⏩ Running pending migrations to {target}...")
            if target == "head":
                command.upgrade(alembic_cfg, "head")
            else:
                command.upgrade(alembic_cfg, target)
            logger.info("✅ Upgrade completed successfully")

        elif action in ("downgrade", "rollback"):
            logger.info(f"⏪ Running downgrade to {target}...")
            command.downgrade(alembic_cfg, target)
            logger.info("✅ Downgrade completed successfully")

        else:
            logger.error(f"❌ Unknown migration action: {action}")
            return False

        # Step 6: Verify final state
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current_rev = context.get_current_revision()
            logger.info(f"📌 Current database revision: {current_rev or '<none>'}")

        return True

    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"❌ Migration failed: {e}", file=sys.stderr, flush=True)
        logger.error(f"❌ Migration failed: {e}", exc_info=True)
        return False


def _parse_runner_args():
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Database migration runner")
    parser.add_argument(
        "--action",
        choices=["upgrade", "downgrade", "rollback"],
        default=os.getenv("MIGRATION_ACTION", "upgrade"),
        help="Migration action to perform (default: upgrade)",
    )
    parser.add_argument(
        "--target",
        default=os.getenv("MIGRATION_TARGET", "head"),
        help="Target revision (default: head)",
    )
    parser.add_argument(
        "--rollback",
        dest="rollback_target",
        help="Shortcut to rollback to a specific target revision",
    )
    return parser.parse_known_args()[0]


if __name__ == "__main__":
    try:
        args = _parse_runner_args()
        if args.rollback_target:
            action = "rollback"
            target = args.rollback_target
        else:
            action = args.action
            target = args.target

        success = run_migrations(action=action, target=target)
        exit_code = 0 if success else 1

        if success:
            logger.info("🎉 Migration process completed successfully")
        else:
            print("💥 Migration process failed", file=sys.stderr, flush=True)
            logger.error("💥 Migration process failed")

        sys.exit(exit_code)

    except KeyboardInterrupt:
        logger.warning("⚠️ Migration interrupted by user")
        sys.exit(130)  # Standard exit code for SIGINT
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"💥 Unexpected error: {e}", file=sys.stderr, flush=True)
        logger.error(f"💥 Unexpected error: {e}", exc_info=True)
        sys.exit(1)
