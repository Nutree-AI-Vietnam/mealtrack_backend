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

### Cloudflare Cache or Integration Event Stalls

**Problem:** A mutation succeeds, but its cache/integration event does not reach
Cloudflare Queue or the Worker.

**Diagnosis:**
```bash
rg -n "hydration.created.v1|cache_invalidation.v1|CloudflareQueuePublisher|IntegrationEventRouter" src workers
```

**Solutions:**
1. For hydration, confirm the business row committed, then check the API log for
   the direct Queue publish result. Hydration no longer waits for the outbox
   worker.
2. Verify the environment-specific Queue ID, account ID, and valid Queue
   credentials. The REST publisher must use the alphanumeric Queue ID, not the
   Worker/Wrangler queue name. Publication is required; there is no disabled
   fallback.
3. Check the Worker logs for `integration_event_ack`,
   `integration_event_retry`, `ack`, `retry`, or DLQ movement on the matching
   `event_id`.
4. For hydration, verify the Worker has valid Upstash Redis REST access,
   `NEON_DATABASE_URL` (or `DATABASE_URL`), and the environment ingress queue.
   A handler failure or database lookup failure retries the whole ingress event.
5. Other mutation paths publish directly through the same Queue publisher;
   inspect the API publication result and the Worker event ID.

**MVP risk note:** if the SQL transaction commits and the direct Queue publish
fails afterward, the hydration row is still durable but the event can be lost.
There is no automatic outbox fallback for that path in this MVP.

**Evidence note:** staging/live deployment proof for this slice is currently pending or blocked on environment credentials. Do not mark the rollout complete until Queue, Worker, and Upstash access are verified separately.

---

### Slow Database Queries

**Problem:** `[SLOW_REQUEST_DETECTED >1s]` in logs

**Diagnosis:**
```bash
SQLALCHEMY_ECHO=true uvicorn src.api.main:app --reload
```

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

**Problem:** OpenAI, Cloudflare Workers AI, Cloudinary, Firebase, RevenueCat, affiliate, or search provider requests timeout

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
**Fix:** Set `DB_CONNECTION_MODE=neon_pooler` and ensure `APP_DATABASE_URL` points to a `-pooler` endpoint.

### direct_pool startup error: pooler URL rejected
**Symptom:** `ConnectionPolicyError: DB_CONNECTION_MODE=direct_pool requires a direct (non-pooler) Neon URL`  
**Cause:** `APP_DATABASE_URL` contains `-pooler` in the hostname but mode is `direct_pool`.  
**Fix:** Either change `APP_DATABASE_URL` to a direct endpoint, or set `DB_CONNECTION_MODE=neon_pooler`.

### DB connection exhaustion (direct_pool)
**Symptom:** `QueuePool limit ... connection timed out`, request latency spikes, or Neon `max_connections` limit hit.
**Fix:** If SQLAlchemy pool utilization is high but Neon has spare capacity, increase `ASYNC_POOL_SIZE_PER_WORKER` or `ASYNC_POOL_MAX_OVERFLOW` and keep DB work in short unit-of-work scopes. If Neon `max_connections` is the bottleneck, reduce `UVICORN_WORKERS` or per-worker pool settings, then consider switching to `neon_pooler` mode. Monitor `/v1/health/db-pool` for checked-out connections and total capacity.

### Migration fails but app works (or vice versa)
**Symptom:** `alembic upgrade head` fails / succeeds independently of the app.  
**Cause:** `DATABASE_URL_DIRECT` (migration URL) and `APP_DATABASE_URL` (app URL) can differ.  
**Fix:** Ensure `DATABASE_URL_DIRECT` points to the direct Neon endpoint for migrations.
