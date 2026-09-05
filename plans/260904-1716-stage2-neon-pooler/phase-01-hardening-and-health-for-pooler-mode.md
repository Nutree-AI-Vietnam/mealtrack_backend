---
phase: 1
title: Hardening and health for pooler mode
status: completed
effort: S
---

# Phase 1: Hardening and health for pooler mode

## Overview

Keep `worker_count` on the neon_pooler policy and enrich `/v1/health/db-pool`
so operators can see mode + workers after cutover (NullPool has no checkout metrics).

## Implementation Steps

1. Pass `worker_count` from `UVICORN_WORKERS` in `neon_pooler` branch of `resolve_connection_policy`.
2. Expand NullPool health payload: `connection_mode`, `pool_type`, `worker_count`, `prepared_statement_cache_size`.
3. Unit test: neon_pooler policy preserves `worker_count`; total_capacity stays 0.
4. Log `DB_CONNECTION_MODE` at process start in `docker-entrypoint.sh`.

## Success Criteria

- [ ] neon_pooler policy exposes worker_count
- [ ] `/health/db-pool` NullPool response includes worker_count
- [ ] Connection policy unit tests pass
