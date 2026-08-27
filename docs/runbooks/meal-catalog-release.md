# Meal Catalog Release Runbook

Use this for Phase 0 catalog-backed recommendations and local-first food search.
Do not paste database URLs, bearer tokens, provider credentials, raw meal
payloads, search text, or user identifiers into release notes.

## Preconditions

- Confirm the target backend image SHA and GitHub Actions checks are green.
- Confirm Render pre-deploy command is `python migrations/run.py`.
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
- `GET /v1/foods/search?q=rice&limit=5` (locale via `Accept-Language`)
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
