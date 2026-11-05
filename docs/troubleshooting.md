# Backend Troubleshooting Guide

**Status:** Evergreen common recovery  
**Incidents / release ops:** `docs/runbooks/` (`provider-outage.md`,
`meal-catalog-incident.md`, `meal-catalog-release.md`)  
**DB connection policy:** `database-guide.md`  
**Integration fail-fast vs degrade:** `external-services.md`

---

## Common Issues

### Import Errors

**Problem:** `ModuleNotFoundError` or circular imports

**Diagnosis:**
```bash
python -c "from src.app.commands.meal.upload_meal_image_immediately_command import UploadMealImageImmediatelyCommand"
```

**Solutions:**
1. Check `__init__.py` exports in package
2. Verify relative imports use `.` notation
3. Check for circular dependencies: A imports B imports A

---

### Database Connection Errors

**Problem:** `OperationalError`, `ConnectionDoesNotExistError`, or failure to connect to PostgreSQL/Neon

**Diagnosis:**
```bash
echo $DATABASE_URL
echo $APP_DATABASE_URL
# App runtime should use a PostgreSQL/Neon URL.
```

**Solutions:**
1. Prefer `APP_DATABASE_URL` for app runtime; keep `DATABASE_URL_DIRECT` for Alembic migration/admin use only.
2. Verify `DB_CONNECTION_MODE=direct_pool` uses a direct Neon host, or `DB_CONNECTION_MODE=neon_pooler` uses a `-pooler` host.
3. For local development, verify the `postgres` service from `docker-compose.yml` is running and healthy.
4. Check firewall/network access rules and Neon connection limits.

---

### Meal Edit/Delete Deadlocks

**Problem:** PostgreSQL reports `DeadlockDetectedError` while a meal is being
edited or deleted.

**Diagnosis:** The meal write path must acquire locks in this order:
`meal -> nutrition -> food_item`. Inspect the endpoint and SQL statements if
the error continues outside these meal mutations.

**Solutions:**
1. Deploy the repository lock-order fix and restart the API workers.
2. Correlate any remaining incident with the endpoint, transaction ID, and
   PostgreSQL lock details before changing pool sizes or adding retries.
3. Do not treat a retry as the primary fix for a repeatable lock-order cycle.

---

### Migration Conflicts

**Problem:** `alembic upgrade head` fails with "heads are not equal"

**Solutions:**
1. `alembic heads` — list all head revisions
2. Merge: `alembic merge -m "Merge heads"`
3. Resolve conflicts in merged migration file, then `alembic upgrade head`

**Prevention:** Pull before creating new migrations; use feature branches.

---

### Authentication Failures

**Problem:** `AuthenticationException` or JWT verification fails

**Diagnosis:**
```bash
ls -la $FIREBASE_CREDENTIALS
```

**Solutions:**
1. Verify Firebase credentials file path and content
2. Check Firebase project ID matches environment
3. Verify token is valid and not expired
4. In dev: set `ENVIRONMENT=development` and `ENABLE_DEV_AUTH_BYPASS=1` to enable `X-Dev-User-Id` header bypass

---

### Redis Connection Issues

**Problem:** `ConnectionError` or Redis not available

**Diagnosis:**
```bash
redis-cli ping
# Should return: PONG
```

**Solutions:**
1. Check `REDIS_URL` format: `redis://host:port/db`
2. Verify Redis is running
3. Optional caches are bypassed when Redis is unavailable. Features that explicitly require Redis-backed state must fail fast or use a durable store.

---

### Meal Insights, Firebase Cleanup or Profile Caches Lag

**Problem:** A write succeeds, but its follow-up work does not happen: no meal
review appears, a deleted account can still sign in with Firebase, or targets
stay stale after a profile change.

**Diagnosis:** this work runs on background tasks in the API process
(`src/api/services/in_process_integration_events.py`). Search the API logs:
```bash
rg -n "in-process integration event handler failed|meal insight generated|Firebase account cleanup (completed|failed)|cache invalidation failed|No connection available" app.log
```

**Solutions:**
1. Missing meal review: the status read re-requests an overdue insight once
   per 10 minutes (`meal_insight_recovery`). A `meal insight payload invalid`
   or handler failure line means the AI answer was rejected or the provider
   failed; the next recovery request retries it.
2. Firebase account still active: find `Firebase account cleanup failed for
   firebase_uid=...` and finish it with
   `scripts/delete_missed_firebase_accounts.py`. A deploy that stopped the
   process mid-run also leaves the UID only in the "Account deletion
   completed" log line.
3. Stale profile caches: look for `profile cache invalidation failed`; the
   caches expire on their own TTL, and the next profile write purges again.
4. `Redis ... error: No connection available.`: a command waited
   `REDIS_POOL_TIMEOUT` for one of the worker's `REDIS_MAX_CONNECTIONS`
   connections and the cache failed open. Look for a burst of events or a slow
   Redis. Raise `REDIS_MAX_CONNECTIONS` only while connections × workers ×
   instances stays under the Redis plan's limit (50 on the free Render plan).

---

### Backend Memory Climbs Between Deploys

**Problem:** Render memory for `nutree_backend` rises after each deploy:
before worker recycling it went from 0.95 GB to 1.94 GB in six days, against
a 2 GiB (2.15 GB) limit. It jumps on the first day and then slows, which is
Python and glibc keeping their memory high-water mark rather than a leak that
grows with every request.

**What bounds it:**
1. `docker-entrypoint.sh` restarts each worker after
   `WORKER_MAX_REQUESTS` (50,000) plus up to `WORKER_MAX_REQUESTS_JITTER`
   (25,000) requests, about once a day per worker today. The supervisor starts
   a replacement at once; the restart costs about 4 s of CPU (app import) and
   can drop a request or two that raced a closing keep-alive connection.
   Look for `Maximum request limit of ... exceeded` in the logs.
2. The image sets `MALLOC_ARENA_MAX=2`, which stops each thread from keeping
   its own malloc arena.

**Solutions:**
1. Memory still near the limit: lower `WORKER_MAX_REQUESTS`, or move to a
   plan with more memory.
2. Restarts hurt more than the memory: raise `WORKER_MAX_REQUESTS`, or set it
   to `0` and deploy at least weekly.
3. Never set `UVICORN_LIMIT_MAX_REQUESTS`: uvicorn reads it directly, and a
   value of `0` makes every worker exit at once.

---

### Slow Database Queries

**Problem:** `[SLOW_REQUEST_DETECTED >1s]` in logs

**Diagnosis:**
```bash
SQLALCHEMY_ECHO=true uvicorn src.api.main:app --reload
```
On production, `pg_stat_statements` is enabled on Neon (since 2026-11-05):
rank statements by `total_exec_time` there before guessing. Its counters
reset when the compute restarts, while `pg_stat_user_tables` counts since
2026-04-13, so a high `seq_scan` total there can be old history; compare two
readings a few minutes apart.

**Solutions:**
1. Add indexes on frequently filtered columns
2. Use `joinedload` to avoid N+1 queries
3. Profile with `EXPLAIN ANALYZE` via SQLAlchemy `text()`
4. Consider Redis caching for frequently read data

---

### Event Bus Handler Not Called

**Problem:** Command/query dispatched but handler not invoked

**Diagnosis:**
```bash
rg "@handles" src/app/handlers/
```

**Solutions:**
1. Verify `@handles` decorator on handler class
2. Check handler is imported in the event bus init
3. Verify command/query class name matches exactly
4. Ensure event bus is singleton (not recreated per request)

---

### External Service Timeouts

**Problem:** OpenAI, Cloudflare Workers AI, Firebase, RevenueCat, affiliate, or search provider requests timeout

**Solutions:**
1. Verify provider-specific timeout settings in `src/infra/config/settings.py`.
2. Check circuit-breaker logs for `[AI-ATTEMPT-FAILED]` and `[AI-FALLBACK-SUCCESS]`.
3. Cache only optional read/cost-optimization data where DB/API fallback is correct.
4. Keep required integrations fail-fast or backed by durable retry state such as an outbox.

---

### Detecting Duplicate ERROR Logs

**Symptom:** A single failed request generates two or more Sentry issues, or `ERROR` appears in both middleware and exception handler logs for the same request.

The single-owner rule means exactly one `ERROR` per unexpected failure. Use these commands to find violations:

```bash
# Verify no direct sentry_sdk outside the connector
rg "import sentry_sdk|sentry_sdk\." src/

# Find log-and-rethrow patterns (should be 0 in route files)
rg "logger\.(error|exception)" src/api/routes/

# Find handlers that log before re-raising (should be 0)
rg -A2 "logger\.error" src/app/handlers/ | grep -A1 "raise"

# Check sensitive data in logs
rg "food_item_changes|image_url|\"Authorization\"|Bearer|Email sent to" src/ tests/

# Run architecture guardrails (bans sentry_sdk outside connector, log-and-rethrow, sensitive substrings)
pytest tests/unit/architecture/ -q
```

**Fix:** Remove `logger.error(...)` from catch blocks that re-raise — let `src/api/exception_handlers.py` own the single root-cause ERROR. Background handlers that swallow exceptions keep their own `ERROR` + `capture_exception`.

---

### Sentry Events Missing

**Problem:** Expected production errors do not appear in Sentry

**Diagnosis:**
```bash
echo $SENTRY_DSN
rg "import sentry_sdk|sentry_sdk\\." src
```

**Solutions:**
1. Verify `SENTRY_DSN` is set in the runtime environment.
2. Verify startup calls `initialize_observability()` before `FastAPI(...)`.
3. Keep direct SDK usage isolated to `src/infra/monitoring/sentry.py`; cron and service code should call the facade.
4. For swallowed cron failures, call `capture_exception(...)` and `flush_observability(timeout=5)` before process exit.
5. Do not attach raw request bodies, auth headers, Firebase claims, emails, food payloads, raw image URLs, provider payloads, or secrets.

### Sentry Logs or Metrics Missing

**Problem:** Sentry error events appear, but Sentry Logs or application metrics are missing.

**Diagnosis:**
```bash
echo $SENTRY_ENABLE_LOGS
echo $SENTRY_ENABLE_METRICS
rg "log_event|increment_metric|gauge_metric|distribution_metric" src
```

**Solutions:**
1. Set `SENTRY_ENABLE_LOGS=true` for Sentry Logs ingestion.
2. Set `SENTRY_ENABLE_METRICS=true` for application metric ingestion.
3. Emit structured logs and metrics through `src.observability`; Python `logging.info(...)` is not the same as a Sentry Logs facade call.
4. Keep attributes allowlisted and scalar. Non-allowlisted, `None`, list, and dict attributes are dropped before reaching Sentry.

---

### CORS Errors

**Problem:** Browser blocks cross-origin requests

**Diagnosis:**
```bash
curl -i -X OPTIONS "http://localhost:8000/v1/meals"
```

**Solutions:**
1. Set `ALLOWED_ORIGINS` to a comma-separated list, for example `http://localhost:3000,https://app.example.com`.
2. Production: restrict `ALLOWED_ORIGINS` to known origins only.
3. Verify preflight (OPTIONS) returns 200

---

## Testing & Debugging

```bash
# Run specific test with output
pytest -v -s tests/unit/domain/services/test_meal_service.py

# Run by name pattern
pytest -k "test_tdee_calculation_with_body_fat" -v

# Debug breakpoint
import pdb; pdb.set_trace()
```

---

See related: `code-standards.md`, `testing-standards.md`, `system-architecture.md`,
`database-guide.md`, `runbooks/`

---

## Neon Connection Mode Issues

### asyncpg prepared statement error in pooler mode
**Symptom:** `asyncpg.exceptions.InvalidSQLStatementNameError: prepared statement ... does not exist`  
**Cause:** Using a Neon `-pooler` URL without `DB_CONNECTION_MODE=neon_pooler`. PgBouncer transaction mode invalidates prepared statements between connections.  
**Fix:** Set `DB_CONNECTION_MODE=neon_pooler` and ensure `APP_DATABASE_URL` points to a `-pooler` endpoint. Full cutover steps: `docs/runbooks/neon-pooler-cutover.md`.

### direct_pool startup error: pooler URL rejected
**Symptom:** `ConnectionPolicyError: DB_CONNECTION_MODE=direct_pool requires a direct (non-pooler) Neon URL`  
**Cause:** `APP_DATABASE_URL` contains `-pooler` in the hostname but mode is `direct_pool`.  
**Fix:** Either change `APP_DATABASE_URL` to a direct endpoint, or set `DB_CONNECTION_MODE=neon_pooler`.

### DB connection exhaustion (direct_pool)
**Symptom:** `QueuePool limit ... connection timed out`, request latency spikes, or Neon `max_connections` limit hit.

**Common software causes (check before raising pool size):**
1. **Nested UoW checkout** — Handler A holds a connection, then calls Handler B which opens another `AsyncUnitOfWork`. Under concurrency the pool fills with holders waiting for a second connection (classic deadlock). Fix: resolve nested work (e.g. TDEE) *before* opening the outer UoW; never open a second session while one is held. See `get_daily_macros_query_handler` / `get_weekly_budget_query_handler`.
2. **Shared singleton UoW** — `event_bus` wired with `uow=AsyncUnitOfWork()` reuses one instance and its `asyncio.Lock`, serializing all requests for that handler. Fix: wire `uow_factory=AsyncUnitOfWork` so each request gets a fresh UoW.

**Capacity fixes:** If SQLAlchemy pool utilization is high but Neon has spare capacity, increase `ASYNC_POOL_SIZE_PER_WORKER` or `ASYNC_POOL_MAX_OVERFLOW` and keep DB work in short unit-of-work scopes. If Neon `max_connections` is the bottleneck, reduce `UVICORN_WORKERS` or per-worker pool settings, then follow `docs/runbooks/neon-pooler-cutover.md` (Stage 2). Monitor `/v1/health/db-pool` for checked-out connections and total capacity.

### Migration fails but app works (or vice versa)
**Symptom:** `alembic upgrade head` fails / succeeds independently of the app.  
**Cause:** `DATABASE_URL_DIRECT` (migration URL) and `APP_DATABASE_URL` (app URL) can differ.  
**Fix:** Ensure `DATABASE_URL_DIRECT` points to the direct Neon endpoint for migrations.
