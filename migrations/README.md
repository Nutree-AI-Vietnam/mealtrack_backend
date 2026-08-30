# Migrations

Database migrations using Alembic with 14-digit sequential timestamp-based naming.

## Quick Start

```bash
# Check current status
./scripts/development/migrate.sh status

# Generate new migration after model changes
./scripts/development/migrate.sh generate "Add user preferences"

# Test migration locally (upgrade -> downgrade -> upgrade)
./scripts/development/migrate.sh test

# Apply migrations
./scripts/development/migrate.sh upgrade

# Rollback last migration
./scripts/development/migrate.sh downgrade

# Rollback to specific target revision
./scripts/development/migrate.sh rollback 20260829000001

# Validate naming, single head, and downgrade support
./scripts/development/migrate.sh check-downgrades
```

## Commands

| Command | Description |
|---------|-------------|
| `status` | Show current revision, pending migrations, and orphan diagnostics |
| `generate <msg>` | Create new migration with autogenerate and 14-digit sequential ID |
| `upgrade [--target <rev>]` | Apply pending migrations (default: `head`) |
| `downgrade [--steps <n>]` | Rollback migrations by step count (default: 1) or target |
| `rollback <target_rev>` | Rollback database to a specific target revision |
| `test` | Run upgrade -> downgrade -> upgrade cycle |
| `check-downgrades` | Validate migration naming, single head, and downgrade methods |

## Migration File Naming

New migrations use a 14-digit UTC timestamp for both the revision ID
and filename: `YYYYMMDDHHmmss_slug.py`.

Example: `20260829000002_add_users_weekly_auto_adjust.py`

- `YYYY`: 4-digit year (e.g. `2026`)
- `MM`: 2-digit month (e.g. `08`)
- `DD`: 2-digit day (e.g. `29`)
- `HH`: 2-digit hour in UTC (e.g. `00`)
- `mm`: 2-digit minute in UTC (e.g. `00`)
- `SS`: 2-digit second in UTC (e.g. `02`)

Existing historical migrations (001-059 and legacy 20-digit merges) remain unchanged.

## Safe Migration & Rollback Flow

When rolling back a deployment or reverting code that includes database migrations, follow one of the two standard procedures to avoid orphaned revisions and broken migration trees:

### Procedure A: Downgrade-First Flow (Recommended for live rollback)
1. **Run Database Rollback First**: Before rolling back the application code / container image, execute the database rollback while the migration script still exists in the codebase:
   ```bash
   python migrations/cli.py rollback <target_revision>
   # or via GitHub Actions 'Database Migration & Rollback' workflow
   ```
2. **Rollback Application Code**: Redeploy the previous application container image or check out the previous Git commit.

### Procedure B: Forward-Fix Reversion Flow (When code is reverted in Git)
If code is reverted in Git after deployment:
1. **Do NOT delete the migration file**: Deleting the migration file from Git removes the revision from Alembic's history, causing errors if any database was already upgraded.
2. **Generate a Compensating Forward Migration**: Create a new forward migration that reverses the schema changes:
   ```bash
   ./scripts/development/migrate.sh generate "Revert feature x"
   ```
3. Deploy this forward migration normally.

## First Time Setup

If this is a new database:

```bash
# Run ONCE to initialize the database
python scripts/init_postgres_db.py
```

## Production Deployment & Rollbacks

For production upgrades and rollbacks:

```bash
# Upgrade to head (default)
python migrations/run.py

# Rollback to a specific target revision
python migrations/run.py --rollback 20260829000001
# or via environment variables
MIGRATION_ACTION=downgrade MIGRATION_TARGET=20260829000001 python migrations/run.py
```

## Important Notes

- Always review generated migrations before applying
- Run `test` command locally before deploying
- Ensure all migrations include an active, tested `downgrade()` implementation
- The baseline migration (`001_initial_schema.py`) is a real schema revision
- `python migrations/run.py` upgrades empty databases from base to head and refuses to stamp an existing unversioned schema automatically

