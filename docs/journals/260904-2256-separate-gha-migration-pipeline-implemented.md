---
date: 2026-09-04
title: Implemented separate GHA migration pipeline
---

# Implemented separate GHA migration pipeline

## Context

Needed Alembic off Render pre-deploy so code image rollback stays possible.

## What shipped

- `.github/workflows/migrate.yml` — manual dispatch, `staging`/`production` Environments, dry-run status vs apply
- Removed `preDeployCommand` from `render.yaml`
- `docker-entrypoint.sh` skips migrate when `RENDER=true` (and production ENV)
- Entrypoint unit tests cover Render skip (15 passed)
- Runbook `docs/runbooks/schema-migration.md` + README/archive/migrations docs updated

## Ops still required

Create GitHub Environments with `DATABASE_URL_DIRECT`, enable prod reviewers, clear any dashboard Pre-Deploy Command, then dry-run/apply on staging.

## Next

Merge + cutover checklist in the runbook; no commit made unless requested.
