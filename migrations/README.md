# Migrations

Database migrations using Alembic with timestamp-based naming.

**Current head:** `20260727000001`

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
```

## Commands

| Command | Description |
|---------|-------------|
| `status` | Show current revision and pending migrations |
| `generate <msg>` | Create new migration with autogenerate |
| `upgrade` | Apply all pending migrations |
| `downgrade` | Rollback the last migration |
| `test` | Run upgrade -> downgrade -> upgrade cycle |

## Migration File Naming

New migrations use a UTC timestamp through microseconds for both the revision ID
and filename: `YYYYMMDDHHMMSSffffff_slug.py`. This prevents separate branches
from generating the same date-based revision ID.

Example: `20260502143022123456_add_user_preferences.py`

Existing migrations (001-058) remain unchanged.

## First Time Setup

If this is a new database:

```bash
# Run ONCE to initialize the database
python scripts/init_postgres_db.py
```

## Production / Staging Deployment

Do **not** run migrations from Render pre-deploy. Use the GitHub Actions
**Migrate Database** workflow (see `docs/runbooks/schema-migration.md`).

Locally or break-glass (direct Neon URL):

```bash
export DATABASE_URL_DIRECT='postgresql://...'
python migrations/cli.py status
python migrations/run.py
```

## Important Notes

- Always review generated migrations before applying
- Run `test` command locally before deploying
- The baseline migration (`001_initial_schema.py`) is a real schema revision
- `python migrations/run.py` upgrades empty databases from base to head and refuses to stamp an existing unversioned schema automatically
