---
title: Separate GHA migration pipeline
date: 2026-09-04
status: approved
repo: mealtrack_backend
---

# Separate GHA migration pipeline — brainstorm

## Summary

Decouple Alembic schema upgrades from Render deploys. Migrations become a **separate GitHub Actions workflow**; Render only runs application code. Goal: rollback previous GHCR image without schema already advanced by the same deploy.

## Problem

- Today prod (and staging docs) run `python migrations/run.py` as Render `preDeployCommand`.
- Non-prod containers also auto-migrate via `docker-entrypoint.sh` unless `AUTO_MIGRATE=false`.
- Coupled migrate+deploy blocks safe **code** rollback: new schema + old image.

## Requirements (locked)

| Item | Decision |
|------|----------|
| Expected output | New `.github/workflows/migrate.yml` + remove Render migrate ownership + entrypoint/docs updates |
| Acceptance | Image deploy/rollback never runs Alembic; migrate only via separate Actions run; prod has Environment approval |
| Scope | Staging + production Neon DBs; separate pipeline only |
| Out of scope | Seeds/backfills, Alembic downgrade-from-CI, auto Render deploy after migrate, folding into `release-pipeline.yml` |
| Constraints | Reuse `python migrations/run.py` + `DATABASE_URL_DIRECT` (`migrations/utils.py`); no dual owners |
| Touchpoints | `render.yaml`, `docker-entrypoint.sh`, new `migrate.yml`, README / render-cd / runbook |

## Approaches evaluated

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| A. Manual separate migrate workflow | Clear ownership; max rollback freedom | Easy to forget migrate before needing new schema | **Chosen** |
| B. Release-gated migrate inside release pipeline | Harder to skip | Couples release to schema; user rejected | Rejected |
| C. Keep Render preDeploy | Zero new ops | Blocks code rollback | Rejected |

## Approved design

1. **Add** `.github/workflows/migrate.yml`
   - Trigger: `workflow_dispatch` only
   - Inputs: `environment` (`staging` \| `production`), optional `dry_run` (status/check only)
   - Job: checkout → setup Python 3.13 → install `requirements.txt` → `python migrations/run.py` (or status if dry_run)
   - GitHub Environments `staging` / `production` hold `DATABASE_URL_DIRECT`; production requires reviewer

2. **Remove** `preDeployCommand: python migrations/run.py` from `render.yaml`

3. **Hard-disable** auto-migrate on Render
   - Prefer: skip migrations when `RENDER=true` in `docker-entrypoint.sh` (defense in depth)
   - Also document dashboard: `AUTO_MIGRATE=false` on staging/prod

4. **Docs**
   - Short runbook: migrate vs deploy order; rollback = previous image SHA; no schema downgrade from this pipeline
   - Update README + archive `render-cd` pointers so they stop claiming preDeploy owns migrate

### Operator flow

```text
schema needed? → Actions → Migrate → pick env → (approve) → run.py
code needed?   → build/push image → Render deploy SHA
bad code?      → Render → previous SHA (schema unchanged)
```

### Risk note

Separate pipeline enables **code** rollback only. Destructive/contract migrations remain non-rollback-friendly; keep expand-migrate-contract discipline.

## Success metrics

- [ ] Render deploy does not execute Alembic
- [ ] Manual migrate workflow succeeds against staging with Environment secrets
- [ ] Prod migrate requires approval gate
- [ ] Docs match actual ownership (GHA migrate, Render code)

## Next

Hand off to `/ck:plan` (default) using this report as context.
