# Meal Catalog Release Runbook

Use this for Phase 0 catalog-backed recommendations and local-first food search.
Do not paste database URLs, bearer tokens, provider credentials, raw meal
payloads, search text, or user identifiers into release notes.

## Preconditions

- Confirm the target backend image SHA and GitHub Actions checks are green.
- Apply schema through GitHub Actions **Migrate Database** and keep Render's
  pre-deploy migration command cleared; see [schema migration](./schema-migration.md).
- Confirm the database is PostgreSQL/Neon-compatible and extensions are enabled:
  `vector` and `pg_trgm`.
- Confirm `scripts/data/meal-recommendation-recipes.json` and
  `scripts/data/meal-recommendation-resolver-map.json` are available from the
  approved storage location.
- Confirm the public browse contract is ready to serve as documented:
  `popular` requires seeded `popularity_rank` values, and `for_you` can report
  `fallback=true` with `ranking_source=curated` on cold start.

## Release Checks

1. Check Alembic has one head:

```bash
.venv/bin/python - <<'PY'
from alembic.config import Config
from alembic.script import ScriptDirectory
print(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
PY
```

2. Dry-run the production catalog:

```bash
.venv/bin/python scripts/import_catalog_recipe_seeds.py \
  --manifest scripts/data/meal-recommendation-recipes.json \
  --resolver-map scripts/data/meal-recommendation-resolver-map.json \
  --resolver-report plans/reports/meal-catalog-production-import-report.json \
  --dry-run
```

Required result: `recipe_count=180`, zero validation errors, zero unresolved
ingredient issues, and zero unreviewed near duplicates.

3. Import into staging, then replay:

```bash
.venv/bin/python scripts/import_catalog_recipe_seeds.py \
  --manifest scripts/data/meal-recommendation-recipes.json \
  --resolver-map scripts/data/meal-recommendation-resolver-map.json \
  --resolver-report plans/reports/meal-catalog-production-import-report.json
```

First run must report `inserted=180`, `skipped_existing=0`. Replay must report
`inserted=0`, `skipped_existing=180`, with the same `manifest_digest`.

4. Run PostgreSQL integration checks:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
DATABASE_URL="$DATABASE_URL_DIRECT" \
DATABASE_URL_DIRECT="$DATABASE_URL_DIRECT" \
.venv/bin/python -m pytest tests/integration/postgres -o addopts="" -m integration -q
```

5. Run staging load:

```bash
MEALTRACK_LOAD_TEST_TOKEN="$TOKEN" \
locust -f tests/performance/locust_meal_catalog.py --headless \
  -u 50 -r 5 --run-time 10m --host "$STAGING_HOST" \
  --csv /tmp/meal-catalog-baseline
```

Targets: search, detail, and replay p95 <=300 ms; new plan p95 <=1.5 s;
eligible request success >=99.95%.

6. Smoke requests after deploy:

- `GET /health`
- `GET /v1/meal-catalog?feed=popular&limit=5`
- `GET /v1/meal-catalog?feed=for_you&limit=5`
- `GET /v1/meal-catalog/{catalog_id}`
- `GET /v1/foods/search?query=rice&limit=5&language=en`
- `POST /v1/meal-recommendations/three-day` with `Idempotency-Key`
- `GET /v1/meal-recommendations/{plan_id}`
- `GET /v1/meal-recommendations/{plan_id}/slots/{slot_id}`

Verify `feed`, `ranking_source`, and `fallback` on the browse response instead
of inferring readiness from a 200 alone.

Local tests, OpenAPI shape, and import dry-runs are not deployment proof; check
the deployed revision separately before calling the browse surface released.

## Rollback Order

1. Disable the client entry point for catalog recommendations. Phase 0 does not
   currently add a backend `MEAL_RECOMMENDATIONS_ENABLED` gate; do not set an
   unread backend env var and assume traffic is disabled.
2. Restore the previous GHCR image SHA in Render and redeploy.
3. Deactivate only affected additive catalog rows after a reviewed SQL plan.
   Prefer `is_active=false` by `catalog_key` or import batch criteria. Do not
   delete rows.

Do not run destructive production downgrades by default. Use schema rollback
only after the migration owner confirms data impact and the previous app image
requires it.

## Weekly planner catalog optimization

The new schema and local verification do not prove staging readiness or live
latency. Preserve current-week explicit generation, GET auto-generation
compatibility, backend calorie authority, logged-slot conflicts and persisted
pantry/grocery state throughout this rollout.

### Expand and backfill

1. Apply generated migrations `20261003042253928494`,
   `20261003102324493409`, and `20261004125027631936` through the schema
   workflow before enabling new paths.
   Confirm one Alembic head and `catalog_publication_version` row `id=1`.
2. Keep all five flags false initially: `CATALOG_PROJECTIONS_ENABLED`,
   `WEEKLY_PLANNER_SHORT_GENERATION`, `CATALOG_PUBLICATION_FENCING_ENABLED`,
   `CATALOG_DURABLE_PREPARATION_ENABLED`, and
   `CATALOG_PERSISTED_PRESENTATION_ENABLED`. Existing reads remain available;
   database publication/invalidation triggers are installed independently of
   application flags.
3. Set `CATALOG_PROJECTION_DATABASE_URL` securely to the intended async
   PostgreSQL database and run bounded projection backfill:

```bash
.venv/bin/python scripts/development/rebuild_catalog_projections.py \
  --batch-size 100 --max-batches 10
```

Resume with `--after-id` from the last **committed** batch. Repeat until the
script prints `complete`; restarting without a cursor is safe and idempotent.
It never commits a partial batch and accepts at most 500 recipes per batch.
No database URL or secret should be copied into reports.

4. Set `CATALOG_WORKER_DATABASE_URL` securely for the worker (or intentionally
   reuse the normal application URL). If a separate worker endpoint uses Neon
   pooler mode, set `CATALOG_WORKER_DB_CONNECTION_MODE` consistently. Backfill
   preparation jobs and run a separate Python worker:

```bash
.venv/bin/python -m scripts.backfill_catalog_preparation \
  --page-size 100 --max-pages 10 --locales vi,en
.venv/bin/python -m scripts.catalog_preparation_worker \
  --concurrency 2 --global-capacity 4 --lease-seconds 120 \
  --provider-deadline-seconds 60 --locales vi,en
```

Preparation backfill rebuilds each selected projection and enqueues jobs in one
transaction. Its `next_cursor` resumes via `--after-id`; replay deduplicates job
identity by task, recipe, source facet, locale and contract. `--once` runs a
bounded worker pass for a canary. Supply OpenAI/USDA credentials through the
secret store; missing provider dependencies keep canonical presentation and
retry/failure state explicit. Budget worker connections separately: each
process budgets up to `concurrency + 1` connections with no overflow, and global
provider capacity applies across worker replicas.

### Cutover gates

Query active projection completeness before enabling compact browse/selection:

```sql
SELECT
  count(*) FILTER (WHERE p.catalog_meal_id IS NULL OR p.query_dirty
                   OR p.schema_version <> 1) AS invalid_query,
  count(*) FILTER (WHERE p.catalog_meal_id IS NULL OR p.nutrition_dirty
                   OR p.schema_version <> 1) AS invalid_nutrition
FROM meal_catalog c
LEFT JOIN meal_catalog_projection p ON p.catalog_meal_id = c.id
WHERE c.is_active;
```

Both counts must be zero for complete optimized selection. Dirty query data
continues through authoritative fallback; dirty nutrition preserves SQL totals
and hydrates selected IDs. Synchronous invalidation plus fallback protects
legacy/direct publishers, but sustained optimized cutover also requires
complete publisher/reconciliation coverage and measured source-update fanout.

With `CATALOG_PROJECTIONS_ENABLED=true`, standard seed/import, popularity-rank
and admin image writes refresh the affected recipe under the exclusive fence
in the source transaction before commit. This currently covers three
application publication paths. Direct SQL, food-reference, serving/nutrient and
allergen-reference writers synchronously invalidate rather than fully rebuild
every affected recipe. Canonical fallback keeps their filter/order/count and
macro results complete while the worker or bounded reconciler catches up.
The requirement for synchronous complete projection publication across all
writers remains a release gate for sustained optimized cutover; do not describe
dirty fallback as complete synchronous projection coverage. Measure repair
fanout, fallback rate and time to clean after those writes on staging.

Enable one flag at a time on staging. Enable persisted presentation only after
checking clean facet/locale/contract overlays; missing text/micros must fall
back to canonical data without provider calls. Enable publication fencing
before short generation; compact candidate parity and canonical nutrition must
be verified before enabling projected reads. Cover owner/week races with same
and different idempotency keys, atomic replay, publication/withdrawal during
compute, and logging between compute and commit. Check fresh reads after
swaps, grocery edits, stock changes and Undo. Keep code/compiler/test evidence
separate from a deployed SHA and real staging endpoint/device evidence.

Observe fixed-label phase metrics for pool checkout, SQL, mapping, selection,
locks, commit, localization and AI attempts. Measure cold/warm p50/p95/p99,
SQL bytes/counts, event-loop delay, projection fallback rate, worker queue age,
attempts/expired leases, provider admission/timeouts, and lock duration. Measure
deployed pool mode/capacity before changing API or worker replicas.

Local evidence uses PostgreSQL 14 on a disposable localhost database. The
combined integration suite passed 37 cases, including six projection/fence
cases; a subsequent same-transaction publisher regression passed independently.
Unicode and compact hard-filter unit cases passed independently. At 10,000 synthetic recipes,
selection CPU improved while every generated coordinate remained equal to the
baseline. Actual 1,000-row PostgreSQL COUNT/page plans did not justify a new
index. These fixtures do not establish Neon plans, production catalog coverage,
network latency, staging behavior or capacity at concurrent production load.
Repeat EXPLAIN on representative deployed distributions before proposing an
index, and use the generator CLI for any resulting schema change.

### Authenticated workflow load

`scripts/benchmarks/weekly_planner_load.py` measures eight fixed groups:
`current`, `generate`, `plan_patch`, `ai_proposal`, `groceries`, `grocery_patch`,
`slot_log`, and `recipes`. It requires a scenario file containing distinct
preseeded test accounts and sequential HTTP steps; it does not create accounts
or substitute IDs/revisions from earlier responses. Preseed each lane's current
plan, valid patch revision, grocery state, pantry state and loggable slots in the
dedicated environment. Store account JWTs only in the named environment
variables, never in the JSON or reports. A minimal read-only lane is:

```json
{
  "accounts": [{
    "token_env": "TEST_PLANNER_TOKEN_01",
    "locale": "en",
    "workflows": [{
      "group": "current",
      "steps": [{
        "method": "GET",
        "path": "/v1/meal-plans/current?auto_generate=false",
        "expected_status": [200]
      }]
    }]
  }]
}
```

Repeat account entries with distinct token variable names for the requested
number of users. Every step uses a relative `/v1/` path and may supply `json`,
`headers` and `expected_status`. Run reads first against dedicated staging:

```bash
.venv/bin/python scripts/benchmarks/weekly_planner_load.py \
  --scenario /secure/path/planner-read-scenario.json \
  --base-url "$PLANNER_LOAD_BASE_URL" --dedicated-test-target \
  --users 50 --seconds 600
```

Read-only runs permit GET and require `auto_generate=false` on current-plan
reads. For prepared mutation fixtures, add `--allow-test-mutations`; remote
hosts also require `--dedicated-test-target`. Include actual AI proposal calls
in a separate live-provider scenario with the same mutation opt-in, valid
test-account fixtures and deployed provider deadlines. Separate cold and warm
reads, deterministic generation, idempotent generation replay and provider
calls. Repeated static idempotency headers measure replay; they do not measure
fresh generation. Reset/reseed exhausted slot-log and revision-sensitive
fixtures between runs, and expect specified conflict statuses where intentional.

Capture per-group samples, p50/p95/p99, statuses, response bytes and unexpected
responses alongside deployed SHA, flags, pool mode, cache state, worker load and
provider timeout/attempt metrics. The CLI was locally compiled and guard-tested;
authenticated staging and live-provider runs remain pending credentials and
fixtures. Its default 50-user/600-second scenario is a measurement starting
point, not production capacity proof.

### Rollback

Disable optimized flags and stop the dedicated worker, then restore the prior
application image if needed. Retain expanded schema, canonical source tables,
prepared overlays and durable jobs. Source triggers can continue to enqueue
projection repair work while the worker is stopped; monitor backlog and replay
bounded backfill before resuming. Canonical fallback remains available, and
withdrawals continue to affect live source reads.

Do not delete recipe rows, alter user plans, clear the operation ledger or run
production downgrades as routine rollback. The migration owner must review any
schema rollback and ensure no active binary/worker depends on removed tables or
columns. A successful local upgrade/downgrade/upgrade cycle proves migration
mechanics only.
