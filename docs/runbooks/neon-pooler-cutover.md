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
| `DB_CONNECTION_MODE` | `neon_pooler` | Selects `NullPool` + `prepared_statement_cache_size=0` |
| `DATABASE_URL_DIRECT` | Neon **direct** URL (no `-pooler`) | Alembic / `preDeployCommand` only |
| `UVICORN_WORKERS` | Start `4`–`6`, canary up | Safe to raise after pooler is healthy |
| `ASYNC_POOL_*` | Ignored in pooler mode | Leave as-is; do not rely on them |

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
   - Verify `DATABASE_URL_DIRECT` still points at the **direct** endpoint.
4. Deploy (or restart) one service instance.
5. Verify:
   - Logs show `Async engine: NullPool mode=neon_pooler`.
   - `GET /v1/health/db-pool` (monitoring auth) returns
     `connection_mode=neon_pooler`, `pool_type=NullPool`, `prepared_statement_cache_size=0`.
   - Smoke: login / open app / log a meal / weekly budget.
6. Optionally bump `UVICORN_WORKERS` one step at a time; watch Neon connection charts
   and API latency / 5xx.

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
