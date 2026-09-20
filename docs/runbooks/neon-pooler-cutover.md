# Neon PgBouncer pooler cutover (Stage 2)

**Audience:** backend / on-call  
**Goal:** Move app runtime from Neon **direct** connections to the Neon **`-pooler`**
endpoint so many app workers multiplex onto a small set of real Postgres sessions.

**Prerequisite:** Stage 1 concurrency fixes deployed
(`feature/stage1-pool-deadlock-fixes-ab72` — nested UoW + shared singleton UoW removed).
Do **not** raise `UVICORN_WORKERS` on a build that still nests checkouts.

---

## Env matrix

| Variable | Stage 2 value | Notes |
|----------|---------------|-------|
| `APP_DATABASE_URL` | Neon **pooled** URL (`…-pooler.…neon.tech…`) | App runtime only |
| `DB_CONNECTION_MODE` | `neon_pooler` | Selects pooler-safe asyncpg (`prepared_statement_cache_size=0`) |
| `NEON_POOLER_USE_QUEUE_POOL` | `true` | Small per-worker `AsyncAdaptedQueuePool` in front of PgBouncer. Avoids NullPool TLS-per-checkout. |
| `DATABASE_URL_DIRECT` | Neon **direct** URL (no `-pooler`) | Alembic / `preDeployCommand` only |
| `UVICORN_WORKERS` | Keep current (do not raise in this wave) | Raise only after queue-pool + Neon min CU are healthy |
| `ASYNC_POOL_*` | Honored when queue-pool is on | `POOL_SIZE_PER_WORKER` / overflow still apply per worker |

Auto-detect: if `DB_CONNECTION_MODE` is unset and the host contains `-pooler`,
the app selects `neon_pooler` automatically. Prefer setting the mode explicitly
in Render so misconfigured URLs fail fast.

---

## Cutover steps (Render)

1. Confirm Stage 1 is live (no nested weekly-budget TDEE; no shared UoW singletons).
2. In Neon console → Connection details → copy the **Pooled** connection string.
3. In Render → mealtrack-backend → Environment:
   - Set `APP_DATABASE_URL` to the pooled string (keep SSL params Neon provides).
   - Set `DB_CONNECTION_MODE=neon_pooler`.
   - Set `NEON_POOLER_USE_QUEUE_POOL=true`.
   - Verify `DATABASE_URL_DIRECT` still points at the **direct** endpoint.
   - Do **not** raise `UVICORN_WORKERS` in the same change.
4. In Neon → project compute: set autoscaling **min CU = 1** (keep max at 8, suspend off).
5. Deploy (or restart) one service instance.
6. Verify:
   - Logs show `Async engine: AsyncAdaptedQueuePool mode=neon_pooler` (queue-pool on) or `NullPool mode=neon_pooler` (flag off).
   - `GET /v1/health/db-pool` (monitoring auth) returns
     `connection_mode=neon_pooler`, `prepared_statement_cache_size=0`.
   - Smoke: login / open app / log a meal / weekly budget.
7. Only after cheap-endpoint P95 recovers, optionally bump `UVICORN_WORKERS` one step at a time; watch Neon connection charts and API latency / 5xx.

---

## Rollback

1. Set `APP_DATABASE_URL` back to the **direct** Neon URL.
2. Set `DB_CONNECTION_MODE=direct_pool`.
3. Restart / redeploy.
4. Confirm logs show `AsyncAdaptedQueuePool mode=direct_pool` and
   `/v1/health/db-pool` returns `pool_type=QueuePool`.

If you see `InvalidSQLStatementNameError: prepared statement ... does not exist`,
you are on a `-pooler` URL without `neon_pooler` mode — fix mode or URL, do not
“increase pool size”.

---

## Related

- `docs/database-guide.md` — connection policy
- `docs/troubleshooting.md` — QueuePool / pooler symptoms
- `plans/260904-1716-stage2-neon-pooler/` — this Stage 2 plan
