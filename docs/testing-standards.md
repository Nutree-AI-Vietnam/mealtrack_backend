# Backend Testing Standards

**Status:** Evergreen test judgment  
**Hard CI gate:** `pytest tests/unit --cov=src --cov-fail-under=65`  
**Suite discovery:** `pytest tests/unit --collect-only`  
Prefer targeted paths; broad unscoped `pytest` can hit duplicate-package import
collisions.

Meal-scan and parse-text prompt evaluations are separate from HTTP contract
tests and from the unit CI gate. Run the offline checks with:

```bash
python scripts/development/evaluate_meal_analyze_prompt_candidates.py --mode contract
python scripts/development/evaluate_parse_text_nutrition.py --mode offline
pytest tests/unit/domain/services/meal_analysis/test_prompt_eval_loop.py \
  tests/unit/domain/strategies/test_meal_analysis_strategy.py \
  tests/unit/domain/services/test_meal_text_nutrition_eval_loop.py \
  tests/unit/scripts/test_evaluate_meal_analyze_prompt_candidates.py \
  tests/unit/scripts/test_evaluate_parse_text_nutrition.py -v
```

These are **offline contract checks**, not prompt-accuracy evaluations. Meal
scan checks response parsing and schema validation. Parse-text replays the
synthetic fixture corpus through the handler with fixture AI/reference results
to check its output contract. Offline fixture timings do not measure provider
or end-to-end staging speed; offline runs do not establish calorie, portion,
meal-detection, or micronutrient accuracy. Reports default to an OS temporary
file with mode `0600`, refuse overwrite, and contain aggregate data rather than
raw inputs.

Provider-grounded prompt comparison requires a separately reviewed corpus and
frozen baseline/candidate prompt snapshots. For meal scan, the manifest must
include privacy-approved, hashed photos and reviewed food, calorie, macro,
micronutrient, and food/non-food references. For each food case, a complete
nutrition reference includes all five macros and all ten micronutrients with
an explicit known, unknown, or masked state. Every micronutrient must have at
least one known reference in both development and held-out splits. For
parse-text, the manifest also contains reviewed inputs, expected items,
quantities, and calories. A complete comparison corpus has 30 cases per feature: 20
development cases (10 English and 10 Vietnamese) and 10 held-out cases (5 per
language). The evaluator reports nutrition references as incomplete when these
conditions are not met, even when the case-count/language split is balanced.
Compare the development split first; reserve held-out for the final comparison.
Provider runs are paired, capped at 50 generations and 300 seconds, and reports
identify provider observations. Parse-text `provider-only` measures
raw model output without lookups or writes; `live` exercises the handler and
configured lookup providers as a separate staging gate.

Do not run a provider mode until the corpus has passed its required human and
privacy review and the staging run is explicitly authorized. Meal scan requires
`ENVIRONMENT=staging` and `--confirm-provider-evaluation`. Parse-text `live` and
`provider-only` require `ENVIRONMENT=staging`,
`PARSE_TEXT_LIVE_EVAL_ENABLED=true`, and `--confirm-live-staging`. Provider
prompt-comparison modes also require the corresponding `--manifest`,
`--baseline-prompt`, and `--candidate-prompt` files. Parse-text live mode uses
the checked-in fixture corpus and reserves worst-case search/detail capacity
against its 25-call limits; the runner can admit fewer cases than `--max-cases`
requests. Reports written inside the repository must target a git-ignored path.
Never treat a prompt character-count reduction or an offline timing as measured
provider latency or as evidence of better accuracy.

The provider comparison entrypoints are:

```text
scripts/development/evaluate_meal_analyze_prompt_candidates.py --mode provider
scripts/development/evaluate_parse_text_nutrition.py --mode provider-only
```

These lines identify staging-only modes; they are not complete runnable
commands. Supply the reviewed manifest and frozen prompt snapshot paths plus the
confirmation flags above only after the provider-evaluation gate is approved.
Provider calls are never part of CI.

---

## Coverage story (single authority)

| Level | Rule |
|-------|------|
| **Hard gate** | Unit suite must meet **65%** line coverage under the CI command above. This is the only number that fails the pipeline. |
| **Aspirational** | Prefer high coverage on domain services, handlers, and critical user paths. Treat “100% critical / 80%+ new feature” as engineering judgment, not a second CI threshold. |
| **Do not** | Document a competing “overall 70% minimum” as if it were the CI gate. |

---

## Test organization

```
tests/
├── architecture/       # Import boundaries and async-runtime guardrails
├── unit/
│   ├── api/            # Routes, dependencies, middleware, mappers
│   ├── app/            # Handlers, commands, queries, services
│   ├── domain/         # Domain services, entities, policies
│   └── infra/          # Adapters, repositories, event bus, external services
├── integration/
│   ├── api/            # Route endpoints
│   ├── infra/          # Repository, external services
│   ├── postgres/       # Real PostgreSQL catalog/recommendation gates
│   └── routes/         # Route-level integration coverage
├── fixtures/           # Factories, fakes, DB fixtures
└── migrations/         # Alembic migration validation
```

Default `pytest.ini` addopts ignore `tests/integration` and select
`not integration`. Explicit integration runs must clear or override addopts.

---

## Naming and structure

Describe **what + condition + expected result**.

```python
def test_tdee_calculation_with_body_fat_uses_katch_mcardle():
    # Arrange
    ...
    # Act
    ...
    # Assert
    ...
```

---

## Markers and commands

```bash
pytest -m unit
pytest tests/unit --cov=src --cov-fail-under=65
pytest tests/integration -o addopts="" -m integration
TEST_DATABASE_URL=postgresql+asyncpg://nutree:nutree@localhost:5432/nutree_test \
  pytest tests/integration/postgres -o addopts="" -m integration -q
```

PostgreSQL integration tests require `TEST_DATABASE_URL` with
`postgresql+asyncpg://` and refuse SQLite or missing URLs. Fresh schemas use
`scripts/init_postgres_db.py` (enables `vector` and `pg_trgm`). Local food
search tests need `pg_trgm` for `similarity()`.

CI runs `lint-imports` then the unit coverage command. The
`postgres-integration` job uses `pgvector/pgvector:pg16` and only
`tests/integration/postgres`.

---

## Mocking

Mock external services; preserve domain logic. Prefer fakes under `tests/fakes/`
or narrow port doubles over broad patches when testing handlers.

Vector cache: exercise nearest-neighbor against the PostgreSQL `pgvector`
adapter or a narrow port fake. Pinecone is not a runtime adapter.

---

## Fixtures

Shared fixtures: `tests/conftest.py`. Domain-specific: `tests/fixtures/` or
package-level `conftest.py`.

---

## Performance and load

Integration timing assertions are fine without `pytest-benchmark`. Catalog
release load gates use Locust:

```bash
MEALTRACK_LOAD_TEST_TOKEN="$TOKEN" \
locust -f tests/performance/locust_meal_catalog.py --headless \
  -u 50 -r 5 --run-time 10m --host "$STAGING_HOST" \
  --csv /tmp/meal-catalog-baseline
```

Do not print bearer tokens or raw payloads. Commit aggregate stats only when
recording release evidence.

---

## Best practices

- Unit: no DB, no external APIs
- Integration: test DB allowed; mock third parties unless the test is explicitly a provider gate
- Isolation: each test independent
- Async: `async def test_` for async handlers
- Architecture tests own layer and logging guardrails

---

See related: `code-standards.md`, `cqrs-guide.md`, `runbooks/` for release gates
