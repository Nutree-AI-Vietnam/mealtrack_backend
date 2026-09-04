---
title: Stage 2 Neon PgBouncer pooler cutover
description: >-
  Prepare and document production cutover to Neon -pooler +
  DB_CONNECTION_MODE=neon_pooler; separate from Stage 1 app fixes.
status: in-progress
priority: P1
branch: feature/stage2-neon-pooler-ab72
tags:
  - concurrency
  - neon
  - pooler
  - pgbouncer
blockedBy:
  - plans/260904-1547-stage1-pool-deadlock-fixes
blocks: []
created: '2026-09-04T10:16:31.771Z'
createdBy: 'ck:plan'
source: skill
---

# Stage 2 Neon PgBouncer pooler cutover

## Overview

Runtime already supports `neon_pooler` (`NullPool` + `prepared_statement_cache_size=0`).
This plan hardens observability, documents the Render/Neon cutover, and does **not**
flip production env vars automatically.

**Prerequisite:** Stage 1 (`feature/stage1-pool-deadlock-fixes-ab72`) merged or
deployed before enabling pooler + more workers in production.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Hardening and health for pooler mode](./phase-01-hardening-and-health-for-pooler-mode.md) | In Progress |
| 2 | [Cutover runbook and env examples](./phase-02-cutover-runbook-and-env-examples.md) | Pending |
| 3 | [Verify unit tests and PR](./phase-03-verify-unit-tests-and-pr.md) | Pending |

## Dependencies

- Stage 1 pool deadlock fixes (app-layer) — merge before prod cutover
- Neon console: pooled connection string (`-pooler` host)
- Render dashboard: `APP_DATABASE_URL`, `DB_CONNECTION_MODE`, `DATABASE_URL_DIRECT`, `UVICORN_WORKERS`
