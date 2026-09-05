---
title: "Separate GHA migration pipeline"
description: "Move Alembic ownership from Render pre-deploy to a separate GitHub Actions migrate workflow so code image rollback stays safe."
status: completed
priority: P2
effort: 4h
branch: "delivery"
tags: [ci, migrations, render, ops]
blockedBy: []
blocks: []
created: "2026-09-04"
createdBy: "ck:plan"
source: skill
---

# Separate GHA migration pipeline

## Overview

Decouple schema upgrades from Render deploys. Add a **manual-only** GitHub Actions workflow that runs Alembic against Neon via `DATABASE_URL_DIRECT`. Remove Render `preDeployCommand` and hard-skip auto-migrate on Render so deploying/rolling back a GHCR image never advances schema.

**Why:** Today `render.yaml` runs `python migrations/run.py` before promote, and non-prod containers also auto-migrate in `docker-entrypoint.sh`. That couples schema to the deploy and blocks safe **code** rollback (new schema + old image).

**Context:** Approved brainstorm `plans/reports/260904-2238-separate-gha-migration-pipeline-brainstorm.md`.

## Goals

- Schema changes only via Actions → Migrate (`workflow_dispatch`)
- Render only swaps application images
- Prod migrate gated by GitHub Environment approval
- Local Docker can still auto-migrate (DX unchanged)

## Non-goals

- Seeds / backfills / catalog import jobs
- Alembic downgrade from CI
- Auto-trigger Render deploy after migrate
- Folding migrate into `release-pipeline.yml`

## Current ground truth

| Piece | Today |
|-------|--------|
| Render | `render.yaml` → `preDeployCommand: python migrations/run.py` |
| Entrypoint | Skip migrate only if `ENV`/`ENVIRONMENT=production`; else `AUTO_MIGRATE` (default true) runs `migrations/run.py` |
| Runner | `migrations/run.py` (retry + upgrade head) |
| Status | `python migrations/cli.py status` |
| URL | `migrations/utils.py` prefers `DATABASE_URL_DIRECT` |
| Tests | `tests/unit/test_docker_entrypoint.py` |

## Architecture

```text
[ops] Actions → migrate.yml → env(staging|production) → run.py|cli status
[code] push/build GHCR → Render deploy SHA (no Alembic)
[rollback] Render → previous SHA (schema unchanged)
```

**Ownership rule:** exactly one migrate owner (GHA). No dual-run with Render.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Add migrate workflow](./phase-01-add-migrate-workflow.md) | Pending |
| 2 | [Decouple Render from Alembic](./phase-02-decouple-render-from-alembic.md) | Pending |
| 3 | [Docs and cutover checklist](./phase-03-docs-and-cutover-checklist.md) | Pending |

## Dependencies

- GitHub Environments `staging` and `production` must exist with secret `DATABASE_URL_DIRECT` (Neon **direct** host, not pooler) before first real migrate run.
- Production Environment requires required reviewers.
- No cross-plan blockers among unfinished plans (unrelated).

## Risk notes

- Enabling this without removing Render pre-deploy → dual migrate / race. Phase 2 must land with or immediately after Phase 1 before relying on the new flow.
- Separate pipeline enables **code** rollback only. Destructive/contract migrations stay expand→migrate→contract.
- Staging Render services with `ENV!=production` currently auto-migrate on boot — Phase 2 must skip when `RENDER=true`.

## Success criteria (plan-level)

- [ ] `migrate.yml` can dry-run status and apply upgrade for staging
- [ ] Prod migrate requires Environment approval
- [ ] Render deploy path does not execute Alembic
- [ ] Entrypoint unit tests cover Render skip
- [ ] README / render-cd / runbook match GHA ownership

## Handoff

Implement with `/ck:cook` on this plan directory.
