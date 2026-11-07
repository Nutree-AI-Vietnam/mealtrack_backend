# Backend — FastAPI

Process memory for agents. Product and stack detail: `README.md`.

## Commands

```bash
# Dev
uvicorn src.api.main:app --reload

# DB (always use the CLI; never hand-create migration files)
./scripts/development/migrate.sh generate "description"
./scripts/development/migrate.sh upgrade
./scripts/development/migrate.sh status

# Format / lint (pre-commit uses ruff-format)
ruff format src/ tests/ && ruff check src/ && mypy src/

# Default CI-aligned tests — do not run bare unscoped `pytest` (import collisions)
pytest tests/unit --cov=src --cov-fail-under=65
pytest tests/architecture -q
```

## MUST-Follow Rules (Non-Inferable)

**DB migrations = use the CLI generator**
- Always generate via `./scripts/development/migrate.sh generate "<msg>"`
  or `python migrations/cli.py generate "<msg>"`.
- Never hand-craft migration files, revision IDs, or filenames.
- Every migration must implement a valid `downgrade()`.
- A migration that changes a column type (or anything a `SELECT` returns)
  must be applied with `migrate.yml` input `restart_compute: true`. The Neon
  PgBouncer pooler keeps old prepared statements, so every query on that
  table fails with `InvalidCachedStatementError` until the compute restarts;
  redeploying the app does not clear it.

**Calories = backend is source of truth**
- Clients must not re-derive calories.
- Formula, `nutrition_override`, and `food_label` exceptions live in
  `src/domain/services/meal_calorie_service.py`.

**Weekly budget `remaining_days` includes today**
- Mon=7, Tue=6, …, Sun=1
- First-day check: `remaining_days >= 7`

**Write paths purge their own Redis caches**
- Meal/hydration/movement writes await the purge in
  `src/api/services/write_cache_invalidation.py` before responding, in every
  environment. Never rely on the in-process integration event handlers
  (`src/api/services/in_process_integration_events.py`), which run after the
  response, for read-after-write freshness.
- No SCAN on the write path: name exact keys in
  `src/domain/cache/cache_invalidation_operations.py`; a cache keyed by an
  arbitrary range checks `CacheKeys.intake_revision` instead.

**Architecture (process, not essay)**
- Domain has no outer I/O. Layer boundaries: `tests/architecture/` and
  `docs/system-architecture.md`. CQRS conventions: `docs/cqrs-guide.md`.

## Docs load policy

- On demand: `README.md` Quick Links or `docs/codebase-summary.md`
- Live HTTP inventory: OpenAPI `/docs` and `src/api/main.py`
- Active plans: repo-root `plans/`
- **Do not load as authority:** `docs/archive/**`
