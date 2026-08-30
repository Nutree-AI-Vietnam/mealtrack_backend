#!/usr/bin/env python
"""
Migration CLI for generating, testing, applying, and rolling back database migrations.

Usage:
    python migrations/cli.py generate "Add user preferences"
    python migrations/cli.py upgrade [--target <rev>]
    python migrations/cli.py downgrade [--steps <n>] [--target <rev>]
    python migrations/cli.py rollback <target_rev>
    python migrations/cli.py test
    python migrations/cli.py status
    python migrations/cli.py check-downgrades
"""

import argparse
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError

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
    """Generate new migration with 14-digit YYYYMMDDHHmmss ID."""
    message = args.message.strip()

    if not message:
        logger.error("Migration message cannot be empty")
        return 1

    use_autogenerate = not getattr(args, "empty", False)
    logger.info(
        f"Generating migration (autogenerate={use_autogenerate}): {message}"
    )

    try:
        alembic_cfg = get_alembic_config()

        # Generate migration (rev_id assigned in env.py via _timestamp_rev_id)
        command.revision(
            alembic_cfg,
            message=message,
            autogenerate=use_autogenerate,
        )

        logger.info("Migration generated successfully")
        logger.info("Review the generated file in migrations/versions/")
        return 0

    except Exception as e:
        logger.error(f"Failed to generate migration: {e}")
        return 1


def cmd_upgrade(args) -> int:
    """Apply pending migrations up to head or specific target."""
    from migrations.utils import migration_engine as engine

    target = getattr(args, "target", None) or "head"
    logger.info(f"Upgrading database to: {target}...")

    try:
        alembic_cfg = get_alembic_config()

        # Show current state
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            before_rev = context.get_current_revision()

        logger.info(f"Current revision: {before_rev or '<none>'}")

        # Run upgrade
        command.upgrade(alembic_cfg, target)

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
    """Rollback migrations by steps or to a target revision."""
    from migrations.utils import migration_engine as engine

    target = getattr(args, "target", None)
    steps = getattr(args, "steps", None)

    if target:
        downgrade_arg = target
    elif steps:
        downgrade_arg = f"-{steps}"
    else:
        downgrade_arg = "-1"

    logger.info(f"Downgrading database using target: {downgrade_arg}...")

    try:
        alembic_cfg = get_alembic_config()

        # Show current state
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            before_rev = context.get_current_revision()

        if before_rev is None:
            logger.info("No migrations to rollback (database has no revisions)")
            return 0

        logger.info(f"Current revision: {before_rev}")

        # Run downgrade
        command.downgrade(alembic_cfg, downgrade_arg)

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


def cmd_rollback(args) -> int:
    """Rollback database to an explicit target revision."""
    from migrations.utils import migration_engine as engine

    target = args.target_revision.strip()
    if not target:
        logger.error("Target revision cannot be empty")
        return 1

    logger.info(f"Initiating safe rollback to target revision: {target}...")

    try:
        alembic_cfg = get_alembic_config()
        script_dir = ScriptDirectory.from_config(alembic_cfg)

        # Verify target revision exists unless rolling back to base
        if target.lower() != "base":
            try:
                script_dir.get_revision(target)
            except CommandError:
                logger.error(
                    f"Target revision '{target}' not found in migration history"
                )
                return 1

        # Check current revision
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current_rev = context.get_current_revision()

        if current_rev is None:
            logger.info("Database has no applied revisions to rollback")
            return 0

        if current_rev == target:
            logger.info(f"Database is already at target revision: {target}")
            return 0

        logger.info(f"Current revision before rollback: {current_rev}")
        logger.info(f"Target revision:                  {target}")

        command.downgrade(alembic_cfg, target)

        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            final_rev = context.get_current_revision()

        logger.info(f"Rollback successful. Database now at: {final_rev or '<none>'}")
        return 0

    except Exception as e:
        logger.error(f"Rollback failed: {e}")
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


def cmd_status(args) -> int:
    """Show current migration status with orphan revision diagnostics."""
    from migrations.utils import migration_engine as engine

    logger.info("Checking migration status...")

    try:
        alembic_cfg = get_alembic_config()
        script_dir = ScriptDirectory.from_config(alembic_cfg)

        heads = script_dir.get_heads()
        head_revision = script_dir.get_current_head()

        if len(heads) > 1:
            logger.warning(
                f"⚠️ Multiple migration heads detected: {', '.join(heads)}. "
                "Merge heads before deploying!"
            )

        # Get current revision from database
        with engine.connect() as conn:
            context = MigrationContext.configure(conn)
            current_revision = context.get_current_revision()

        logger.info(f"Current revision: {current_revision or '<none>'}")
        logger.info(f"Head revision:    {head_revision or '<none>'}")

        if current_revision is None:
            logger.info("Database has no migrations applied")
            return 0

        # Check if database revision is known to local migration tree
        all_revisions = {rev.revision for rev in script_dir.walk_revisions()}
        if current_revision not in all_revisions:
            logger.warning(
                "\n"
                "===============================================================\n"
                "⚠️  ORPHAN REVISION / DATABASE AHEAD OF CODEBASE DETECTED!\n"
                "===============================================================\n"
                f"The database has revision '{current_revision}', which DOES NOT EXIST\n"
                "in this codebase's migration history.\n\n"
                "This typically happens when a Git commit was reverted or an older\n"
                "container image was deployed without first rolling back the database.\n\n"
                "Remediation Options:\n"
                "  1. Pre-Revert Rollback:\n"
                "     Check out the commit/image that created the migration and run:\n"
                f"     python migrations/cli.py rollback {head_revision}\n\n"
                "  2. Forward-Fix Reversion:\n"
                "     Restore the migration file in git and create a new forward migration\n"
                "     to undo the changes rather than deleting the migration file.\n"
                "==============================================================="
            )
            return 1

        if current_revision == head_revision:
            logger.info("Database is up to date")
        else:
            revisions = list(script_dir.walk_revisions(head_revision, current_revision))
            pending_count = len(revisions)
            logger.info(f"Pending migrations: {pending_count}")
            for rev in revisions:
                logger.info(f"  - {rev.revision}: {rev.doc}")

        return 0

    except Exception as e:
        logger.error(f"Failed to check status: {e}")
        return 1


def cmd_check_downgrades(args) -> int:
    """Validate all migration files for naming rules, single head, and downgrade support."""
    logger.info("Validating database migration scripts...")
    versions_dir = Path("migrations/versions")

    if not versions_dir.exists():
        logger.error(f"Versions directory not found: {versions_dir}")
        return 1

    alembic_cfg = get_alembic_config()
    script_dir = ScriptDirectory.from_config(alembic_cfg)

    # 1. Single Head check
    heads = script_dir.get_heads()
    if len(heads) != 1:
        logger.error(f"Validation failed: Multiple heads detected: {heads}")
        return 1
    logger.info(f"✅ Single head verified: {heads[0]}")

    merge_revs = {
        rev.revision
        for rev in script_dir.walk_revisions()
        if isinstance(rev.down_revision, tuple)
    }

    # 2. File naming and downgrade implementation check
    name_pattern = re.compile(r"^(\d{3}|\d{14}|\d{20})_[\w\-]+\.py$")
    invalid_files: list[str] = []
    missing_downgrades: list[str] = []

    for file_path in sorted(versions_dir.glob("*.py")):
        if file_path.name.startswith("__"):
            continue

        if not name_pattern.match(file_path.name):
            invalid_files.append(file_path.name)

        content = file_path.read_text(encoding="utf-8")

        # Check downgrade function definition
        downgrade_match = re.search(
            r"def downgrade\(\)[^:]*:\s*(.*?)(?=\n\w|\Z)",
            content,
            re.DOTALL,
        )
        if downgrade_match:
            body = downgrade_match.group(1).strip()
            body_without_comments = re.sub(r"#[^\n]*", "", body).strip()
            # If body has no active code, check if it's a documented merge, backfill, or intentional no-op
            if not body_without_comments or body_without_comments == "pass":
                rev_match = re.search(
                    r"revision(?:\s*:\s*str)?\s*=\s*[\'\"]([^\'\"]+)[\'\"]",
                    content,
                )
                rev_id = rev_match.group(1) if rev_match else None
                has_doc_comment = bool(re.search(r"#[^\n]+", body))
                if rev_id not in merge_revs and not has_doc_comment:
                    missing_downgrades.append(file_path.name)
        else:
            missing_downgrades.append(file_path.name)

    if invalid_files:
        logger.error(f"❌ Invalid migration file names found: {invalid_files}")
        return 1

    if missing_downgrades:
        logger.error(
            f"❌ Migration files missing downgrade implementation: {missing_downgrades}"
        )
        return 1

    logger.info(
        f"✅ All {len(list(versions_dir.glob('*.py')))} migration files have valid naming and downgrade implementations."
    )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Migration CLI for database migrations and rollbacks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    gen_parser = subparsers.add_parser(
        "generate", help="Generate new migration with 14-digit YYYYMMDDHHmmss ID"
    )
    gen_parser.add_argument("message", help="Migration message")
    gen_parser.add_argument(
        "--empty",
        action="store_true",
        help="Generate empty migration template without DB autogenerate",
    )
    gen_parser.set_defaults(func=cmd_generate)

    # upgrade
    up_parser = subparsers.add_parser(
        "upgrade", help="Apply pending migrations (default: head)"
    )
    up_parser.add_argument(
        "--target",
        default="head",
        help="Target revision to upgrade to (default: head)",
    )
    up_parser.set_defaults(func=cmd_upgrade)

    # downgrade
    down_parser = subparsers.add_parser(
        "downgrade", help="Rollback migrations by steps or target"
    )
    down_parser.add_argument(
        "--steps",
        type=int,
        help="Number of migration steps to rollback (default: 1)",
    )
    down_parser.add_argument(
        "--target",
        help="Target revision to downgrade to",
    )
    down_parser.set_defaults(func=cmd_downgrade)

    # rollback
    rb_parser = subparsers.add_parser(
        "rollback", help="Rollback database to a specific target revision"
    )
    rb_parser.add_argument(
        "target_revision",
        help="Target revision to rollback to (e.g. 20260829000001 or 'base')",
    )
    rb_parser.set_defaults(func=cmd_rollback)

    # test
    test_parser = subparsers.add_parser(
        "test", help="Test upgrade -> downgrade -> upgrade cycle"
    )
    test_parser.set_defaults(func=cmd_test)

    # status
    status_parser = subparsers.add_parser(
        "status", help="Show migration status and orphan diagnostics"
    )
    status_parser.set_defaults(func=cmd_status)

    # check-downgrades
    check_parser = subparsers.add_parser(
        "check-downgrades",
        help="Validate migration naming, single head, and downgrade methods",
    )
    check_parser.set_defaults(func=cmd_check_downgrades)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
