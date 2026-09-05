---
date: 2026-09-04
title: Planned separate GHA migration pipeline
---

# Planned separate GHA migration pipeline

## Context

Brainstorm approved: schema upgrades must leave Render so code image rollback stays possible.

## What happened

Created plan `plans/260904-2239-separate-gha-migration-pipeline/` (3 phases, ~4h):

1. Add dispatch-only `migrate.yml` using `migrations/run.py` / `cli.py status` + GitHub Environments
2. Remove `render.yaml` `preDeployCommand`; skip auto-migrate when `RENDER=true`; extend entrypoint tests
3. Runbook + README/archive render-cd cutover docs

## Decisions

- Separate workflow only (not folded into release pipeline)
- `DATABASE_URL_DIRECT` is the required Environment secret
- Local `AUTO_MIGRATE` DX kept; Render never owns Alembic after cutover

## Next

`/ck:cook` on the plan, or validate/red-team first if desired.
