---
phase: 1
title: "Add migrate workflow"
status: completed
priority: P1
effort: 1.5h
dependencies: []
---

# Phase 1: Add migrate workflow

## Overview

Create `.github/workflows/migrate.yml` as the sole production-grade schema apply path. Manual `workflow_dispatch` only — never on push/PR, never called from `release-pipeline.yml`.

## Requirements

- Functional: choose `staging` or `production`; optional `dry_run` runs status only
- Non-functional: use GitHub Environments for secrets + prod approval; fail closed if `DATABASE_URL_DIRECT` missing
- Constraint: reuse existing runners — `python migrations/run.py` (apply) and `python migrations/cli.py status` (dry run)

## Architecture

```yaml
on:
  workflow_dispatch:
    inputs:
      environment: { staging | production }   # maps to GitHub Environment name
      dry_run: { boolean, default false }     # status vs upgrade
jobs:
  migrate:
    environment: ${{ inputs.environment }}
    steps:
      - checkout
      - setup-python 3.13.2 (match release-pipeline)
      - pip install -r requirements.txt
      - assert DATABASE_URL_DIRECT set
      - dry_run? cli status : migrations/run.py
```

Secret mapping (Environment secret → process env):

- `DATABASE_URL_DIRECT` → `DATABASE_URL_DIRECT` (required)
- Optional fallback already supported by `migrations/utils.py`: `DATABASE_URL` if direct unset (prefer not to rely on pooler)

## Related Code Files

- Create: `.github/workflows/migrate.yml`
- Reference: `migrations/run.py`, `migrations/cli.py`, `migrations/utils.py`
- Reference style: `.github/workflows/release-pipeline.yml` (Python 3.13.2 + pip cache)

## Implementation Steps

1. Add `migrate.yml` with `workflow_dispatch` inputs `environment` (choice) and `dry_run` (boolean).
2. Single job `migrate` with `environment: ${{ inputs.environment }}`, `permissions: contents: read`.
3. Steps: checkout → setup-python `3.13.2` → cache pip → `pip install -r requirements.txt`.
4. Export env:
   ```bash
   DATABASE_URL_DIRECT: ${{ secrets.DATABASE_URL_DIRECT }}
   ```
   Fail early if empty:
   ```bash
   test -n "$DATABASE_URL_DIRECT"
   ```
5. Branch command:
   - `dry_run == true` → `python migrations/cli.py status`
   - else → `python migrations/run.py`
6. Write a short `$GITHUB_STEP_SUMMARY` with environment, mode, and exit outcome.
7. Do **not** trigger Render, build images, or call other workflows.

## Ops prerequisite (document in Phase 3; configure before first use)

- GitHub → Settings → Environments: create `staging`, `production`
- Each env: secret `DATABASE_URL_DIRECT` = Neon direct connection string
- `production`: enable required reviewers

## Success Criteria

- [ ] Workflow file exists and is dispatch-only
- [ ] Job binds to selected GitHub Environment
- [ ] Dry run invokes `migrations/cli.py status`
- [ ] Apply invokes `migrations/run.py`
- [ ] Missing `DATABASE_URL_DIRECT` fails before Alembic
- [ ] No coupling to `release-pipeline.yml` / `ghcr-build-push.yml`

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Wrong DB URL (pooler) | Require `DATABASE_URL_DIRECT`; utils strips `-pooler` only as fallback |
| Accidental prod migrate | Environment protection + reviewers; dry_run default false but status available first |
| Workflow runs on every push | `workflow_dispatch` only — no `push`/`pull_request` triggers |
