#!/usr/bin/env python
"""
Migration CLI for generating, testing, and applying database migrations.

Usage:
    python migrations/cli.py generate "Add user preferences"
    python migrations/cli.py upgrade
    python migrations/cli.py downgrade
    python migrations/cli.py test
    python migrations/cli.py status
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import Script, ScriptDirectory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

ALEMBIC_CONFIG_PATH = "alembic.ini"


def get_alembic_config() -> Config:
    """Load Alembic configuration with database URL set."""
    from migrations.utils import MIGRATION_URL

    config_path = Path(ALEMBIC_CONFIG_PATH)
    if not config_path.exists():
        logger.error(f"Alembic config not found: {config_path.absolute()}")
        sys.exit(1)

    alembic_cfg = Config(str(config_path))
    alembic_cfg.set_main_option("sqlalchemy.url", MIGRATION_URL)
    return alembic_cfg


def cmd_generate(args) -> int:
    """Generate new migration with autogenerate."""
    message = args.message.strip()

    if not message:
        logger.error("Migration message cannot be empty")
        return 1

    logger.info(f"Generating migration: {message}")

    try:
        alembic_cfg = get_alembic_config()

        # Generate migration with autogenerate
        command.revision(
            alembic_cfg,
            message=message,
            autogenerate=True,
        )

        logger.info("Migration generated successfully")
        logger.info("Review the generated file in migrations/versions/")
        return 0

    except Exception as e:
        logger.error(f"Failed to generate migration: {e}")
        return 1


def cmd_upgrade(args) -> int:
    """Apply pending migrations."""
    from migrations.utils import migration_engine as engine

    logger.info("Upgrading database...")

    try:
        alembic_cfg = get_alembic_config()

        # Show current state
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            before_rev = context.get_current_revision()

        logger.info(f"Current revision: {before_rev or '<none>'}")

        # Run upgrade
        command.upgrade(alembic_cfg, "head")

        # Show new state
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            after_rev = context.get_current_revision()

        if before_rev == after_rev:
            logger.info("No pending migrations")
        else:
            logger.info(f"Upgraded to: {after_rev}")

        logger.info("Upgrade completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Upgrade failed: {e}")
        return 1


def cmd_downgrade(args) -> int:
    """Rollback last migration."""
    from migrations.utils import migration_engine as engine

    logger.info("Downgrading database...")

    try:
        alembic_cfg = get_alembic_config()

        # Show current state
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            before_rev = context.get_current_revision()

        if before_rev is None:
            logger.info("No migrations to rollback")
            return 0

        logger.info(f"Current revision: {before_rev}")

        # Run downgrade by one step
        command.downgrade(alembic_cfg, "-1")

        # Show new state
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            after_rev = context.get_current_revision()

        logger.info(f"Downgraded to: {after_rev or '<none>'}")
        logger.info("Downgrade completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Downgrade failed: {e}")
        return 1


def cmd_test(args) -> int:
    """Test migration cycle: upgrade -> downgrade -> upgrade."""
    from migrations.utils import migration_engine as engine

    logger.info("Testing migration cycle...")

    try:
        alembic_cfg = get_alembic_config()

        # Get initial state
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            initial_rev = context.get_current_revision()

        script_dir = ScriptDirectory.from_config(alembic_cfg)
        head_revision = script_dir.get_current_head()

        logger.info(f"Initial revision: {initial_rev or '<none>'}")
        logger.info(f"Head revision:    {head_revision or '<none>'}")

        # Step 1: Upgrade
        logger.info("Step 1/3: Upgrading...")
        command.upgrade(alembic_cfg, "head")

        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            after_upgrade = context.get_current_revision()
        logger.info(f"After upgrade: {after_upgrade}")

        # Step 2: Downgrade
        logger.info("Step 2/3: Downgrading...")
        command.downgrade(alembic_cfg, "-1")

        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            after_downgrade = context.get_current_revision()
        logger.info(f"After downgrade: {after_downgrade or '<none>'}")

        # Step 3: Upgrade again
        logger.info("Step 3/3: Upgrading again...")
        command.upgrade(alembic_cfg, "head")

        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            final_rev = context.get_current_revision()
        logger.info(f"Final revision: {final_rev}")

        if final_rev == head_revision:
            logger.info("Migration test PASSED")
            return 0
        else:
            logger.error(
                f"Migration test FAILED: expected {head_revision}, got {final_rev}"
            )
            return 1

    except Exception as e:
        logger.error(f"Migration test FAILED: {e}")
        return 1


def pending_upgrade_revisions(
    script_dir: ScriptDirectory,
    current_revision: str | None,
    head_revision: str,
) -> list[Script]:
    """Revisions ``upgrade head`` would apply from ``current_revision``.

    Uses the same merge-aware iterator as Alembic's upgrade command.
    ``walk_revisions(head, current)`` raises when current is an ancestor
    through multiple merge paths, which is a normal state for this graph.
    """
    lower = "base" if current_revision is None else current_revision
    return list(
        script_dir.iterate_revisions(
            head_revision,
            lower,
            implicit_base=True,
            assert_relative_length=False,
        )
    )


def cmd_status(args) -> int:
    """Show current migration status."""
    from migrations.utils import migration_engine as engine

    logger.info("Checking migration status...")

    try:
        alembic_cfg = get_alembic_config()
        script_dir = ScriptDirectory.from_config(alembic_cfg)

        heads = script_dir.get_heads()
        if len(heads) > 1:
            logger.error(
                "Multiple heads detected: %s. Create a merge migration before upgrading.",
                ", ".join(heads),
            )
            return 1

        head_revision = heads[0] if heads else None

        # Get current revision from database
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current_revision = context.get_current_revision()

        logger.info(f"Current revision: {current_revision or '<none>'}")
        logger.info(f"Head revision:    {head_revision or '<none>'}")

        if head_revision is None:
            logger.info("No migration revisions found in versions/")
            return 0

        if current_revision == head_revision:
            logger.info("Database is up to date")
        elif current_revision is None:
            pending = pending_upgrade_revisions(script_dir, None, head_revision)
            logger.info("Database has no migrations applied")
            logger.info(f"Pending migrations: {len(pending)}")
            for rev in reversed(pending):
                logger.info(f"  -> {rev.revision}")
        else:
            pending = pending_upgrade_revisions(
                script_dir, current_revision, head_revision
            )
            logger.info(f"Pending migrations: {len(pending)}")
            for rev in reversed(pending):
                logger.info(f"  -> {rev.revision}")

        return 0

    except Exception as e:
        logger.error(f"Failed to check status: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Migration CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen_parser = subparsers.add_parser("generate", help="Generate new migration")
    gen_parser.add_argument("message", help="Migration message")
    gen_parser.set_defaults(func=cmd_generate)

    # upgrade
    up_parser = subparsers.add_parser("upgrade", help="Apply pending migrations")
    up_parser.set_defaults(func=cmd_upgrade)

    # downgrade
    down_parser = subparsers.add_parser("downgrade", help="Rollback last migration")
    down_parser.set_defaults(func=cmd_downgrade)

    # test
    test_parser = subparsers.add_parser("test", help="Test upgrade/downgrade cycle")
    test_parser.set_defaults(func=cmd_test)

    # status
    status_parser = subparsers.add_parser("status", help="Show migration status")
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
