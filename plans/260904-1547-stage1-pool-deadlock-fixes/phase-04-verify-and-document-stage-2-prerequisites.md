---
phase: 4
title: Verify and document Stage 2 prerequisites
status: completed
effort: S
---

# Phase 4: Verify and document Stage 2 prerequisites

## Overview

Stage 1 code is on `feature/stage1-pool-deadlock-fixes-ab72`. This phase records
verification evidence and the ops checklist for Stages 2–3 — **no pooler cutover
in this PR**.

## Verification evidence

- Nested TDEE removed from weekly budget; unit tests prove TDEE before UoW enter
- `event_bus.py`: **0** matches for `uow=AsyncUnitOfWork()`
- Weekly budget header+cache path opens zero SQLAlchemy sessions
- Unit suite: **2792 passed** on branch tip

## Stage 2 checklist (500–2k concurrent) — do next, not now

1. Set `APP_DATABASE_URL` to Neon **`-pooler`** hostname
2. Set `DB_CONNECTION_MODE=neon_pooler` (enables `NullPool` + disables prepared stmt cache)
3. Keep `DATABASE_URL_DIRECT` on the **direct** endpoint for Alembic only
4. Bump `UVICORN_WORKERS` carefully; in pooler mode per-worker SQLAlchemy pool knobs are ignored
5. Watch `GET /v1/health/db-pool` and Neon connection metrics during canary
6. Rollback plan: flip mode back to `direct_pool` + direct URL if prepared-statement or latency regressions appear

## Stage 3 checklist (2k–10k+) — follow-on

1. Render horizontal autoscaling (CPU threshold)
2. Raise Redis hit ratio on app-open reads (timezone, hydration, macros, streak, weekly budget, profile)
3. Optional: composite bootstrap endpoint to cut RPS
4. Queue non-critical writes (analytics / insight / notifications)

## Success Criteria

- [x] Troubleshooting doc mentions nested UoW as a QueuePool cause
- [x] Stage 2/3 checklist written; no premature pooler cutover in this plan
