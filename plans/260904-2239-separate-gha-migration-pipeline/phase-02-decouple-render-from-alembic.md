---
phase: 2
title: "Decouple Render from Alembic"
status: completed
priority: P1
effort: 1.5h
dependencies: [1]
---

# Phase 2: Decouple Render from Alembic

## Overview

Remove Alembic from the Render deploy path and stop containers from auto-migrating when running on Render. After this phase, image deploy/rollback cannot advance schema.

## Requirements

- Functional: Render web start never calls `migrations/run.py`
- Functional: Local/non-Render Docker still respects `AUTO_MIGRATE` default true
- Tests: extend `tests/unit/test_docker_entrypoint.py` for Render skip

## Architecture

**Before**

```text
Render preDeploy → migrations/run.py → promote image
entrypoint: production skip; else maybe AUTO_MIGRATE
```

**After**

```text
Render → start image only
entrypoint: if RENDER=true → skip migrate (any ENV)
           elif production ENV → skip migrate (compat)
           else AUTO_MIGRATE gate
```

Prefer `RENDER=true` (Render sets this) as the primary skip so **staging** on Render also stops bootstrapping schema.

## Related Code Files

- Modify: `render.yaml` — remove `preDeployCommand`
- Modify: `docker-entrypoint.sh` — skip when `RENDER=true`; update log text (no longer “pre-deploy handles this” as the primary story)
- Modify: `tests/unit/test_docker_entrypoint.py` — add Render cases; adjust production message assertions if copy changes
- Comment cleanup: `render.yaml` note that migrations are GHA-owned; `DATABASE_URL_DIRECT` still useful for break-glass local ops but not preDeploy

## Implementation Steps

1. Delete `preDeployCommand: python migrations/run.py` from `render.yaml`.
2. Update comment block in `render.yaml` to point at Actions migrate workflow (not preDeploy).
3. Change `docker-entrypoint.sh` skip logic to:
   ```bash
   if [ "${RENDER:-}" = "true" ]; then
     log "⏭️ Skipping migrations (Render; use GitHub Actions migrate workflow)"
   elif [ "${ENV:-}" = "production" ] || [ "${ENVIRONMENT:-}" = "production" ]; then
     log "⏭️ Skipping migrations (production ENV; use GitHub Actions migrate workflow)"
   else
     # existing AUTO_MIGRATE branch unchanged
   fi
   ```
4. Keep `AUTO_MIGRATE` parsing/behavior for local/dev Compose.
5. Tests:
   - `RENDER=true` skips migrate even when `AUTO_MIGRATE=true` and ENV unset
   - `RENDER=true` skips even with invalid `AUTO_MIGRATE` (fail-open to skip, same as production today)
   - Existing production ENV skip still passes (message may change — update substring assert)
   - Non-Render default still runs migrate
6. Run: `pytest tests/unit/test_docker_entrypoint.py -q`

## Success Criteria

- [ ] `render.yaml` has no `preDeployCommand`
- [ ] Entrypoint skips Alembic when `RENDER=true`
- [ ] Local default still migrates
- [ ] Entrypoint unit tests green with new Render cases
- [ ] Production ENV skip retained as secondary guard

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Deploy code that needs new schema without running Actions migrate | Operator runbook (Phase 3); expand-first migrations |
| Staging still auto-migrates if only production ENV checked | Primary skip on `RENDER=true` |
| Dual ownership if dashboard still has manual preDeploy override | Cutover checklist: clear Render dashboard preDeploy field |
| Message assertion churn in tests | Update tests in same PR as entrypoint copy |
