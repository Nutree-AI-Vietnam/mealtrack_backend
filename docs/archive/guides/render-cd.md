# Render CD Flow

> **Superseded (migrations):** Schema upgrades are **not** owned by Render
> pre-deploy anymore. Use GitHub Actions → **Migrate Database** and follow
> [`docs/runbooks/schema-migration.md`](../../runbooks/schema-migration.md).
> This archive page is kept for historical Render CD context only.

## Production Flow

Render deploys application images only:

1. Build Docker image.
2. Start web service (no Alembic in pre-deploy).
3. Health check `/health`.

Apply pending Alembic revisions **before** deploying code that requires them,
via Actions migrate (dry run → apply).

For meal catalog releases, use
[Meal Catalog Release Runbook](../../runbooks/meal-catalog-release.md) after
schema migrate (if needed) and image deploy. That runbook covers manifest
digest, staging import/replay counts, smoke requests, load gates, and
rollback order.

## Render Settings

Production service:

```text
Branch: main
Runtime: Docker
Pre-deploy command: (empty — do not run migrations here)
Docker command: /app/docker-entrypoint.sh
Health check path: /health
```

Staging service:

```text
Branch: delivery
Runtime: Docker
Pre-deploy command: (empty — do not run migrations here)
Docker command: /app/docker-entrypoint.sh
Health check path: /health
```

## Environment Variables

Use these deployment-related variables:

```text
UVICORN_WORKERS=1
MIGRATION_LOCK_TIMEOUT_MS=10000
MIGRATION_STATEMENT_TIMEOUT_MS=240000
```

`docker-entrypoint.sh` skips migrations when `RENDER=true` or
`ENV`/`ENVIRONMENT=production`. Schema apply is via Actions
(`DATABASE_URL_DIRECT` on GitHub Environments). Local non-Render containers
may still run `python migrations/run.py` at startup when `AUTO_MIGRATE` is
enabled.

## Why (historical)

Migrations in startup can make the web container fail to bind to Render's
port. Render then restarts the container and repeats the migration attempt.
The old mitigation was pre-deploy migrate. That blocked safe **code** image
rollback once schema had advanced, so migrate moved to a separate Actions
pipeline.

## Emergency Recovery

If a bad **code** deploy is live and schema is already compatible with the
previous image:

1. Restore the previous GHCR SHA on Render.
2. Do **not** re-enable Render pre-deploy migrations as a permanent fix.

If schema itself is broken, use the schema migration runbook break-glass
section and a reviewed manual repair — do not Alembic-downgrade production
unless data ownership has been reviewed.

For catalog-specific incidents, use
[Meal Catalog Incident Runbook](../../runbooks/meal-catalog-incident.md).
Prefer client entry-point disablement, previous GHCR SHA restore, or reviewed
`is_active=false` catalog-row deactivation before considering a production
schema downgrade.
