# Catalog projection and selection verification

Work context: MealTrack backend. Date: 3 October 2026.
Baseline generator: `cbc3152297c5684e509769d0c226eea17d6409c5`.
Scope: local implementation and disposable PostgreSQL evidence; no staging or production deployment claim.

## Delivered interfaces

- `AsyncCatalogMealRepository.get_meal_summaries(ids)` returns selected compact domain summaries. On clean projections it issues one SELECT, without ingredient/food/step hydration; output preserves requested ID order. The live source activation join prevents withdrawn summaries.
- `list_recipe_page(...)` applies casefold search/cuisine, existing browse diets/dislikes, title suitability, cooking time and canonical allergy constraints before SQL COUNT/order/LIMIT. PostgreSQL `C` collation matches Python Unicode code-point order. Browse and planner diets intentionally retain their distinct existing semantics.
- Missing/dirty query fields invoke the full authoritative fallback. Dirty nutrition retains SQL totals/page membership and hydrates only selected page IDs. Selection uses compact candidates only when every active query/nutrition projection is current; otherwise it selects from canonical meals.
- Typed macro/filter columns and canonical allergen links remain separate from the rebuildable JSON presentation payload. Backend macro conversion and calorie calculation remain authoritative. Independent selection, ingredient, translation and enrichment digests cover actual values rather than coarse timestamps.
- `capture_catalog_publication_version()` and `lock_catalog_publication(shared=True|False)` expose independent global facets under a singleton row lock. Generation compares selection facet, so micronutrient-only edits need not restart selection. The integrator owns final generation/idempotency/ordered-slot wiring and generated migrations.
- PostgreSQL statement fencing covers catalog, ingredient, step, allergen registry/link, food-reference, normalized serving and nutrient writes, including direct SQL and TRUNCATE. Source invalidation rejects stale projections; result tables do not invalidate their own source facets. The integrator extends these triggers for durable job publication.
- `CatalogProjectionRebuilder.rebuild(ids<=500)` and `.reconcile_page(after_id,limit)` rebuild under the exclusive fence. `scripts/development/rebuild_catalog_projections.py` selects its database through an explicit environment variable and commits bounded resumable batches.
- When projected reads are enabled, seed/import creation or replacement, popularity-rank publication and admin image publication refresh the affected recipe before committing in the same exclusive-fenced transaction. Projection conflict updates advance `updated_at` using the database clock on PostgreSQL. Direct SQL and shared food/reference writers invalidate synchronously, then use canonical fallback until bounded reconciliation; they do not yet synchronously rebuild every affected recipe.
- Compiled combined Unicode word patterns preserve regex boundary semantics. The deterministic generator precomputes slot eligibility and calorie distances per call; compact candidates retain precomputed hard constraint features. Algorithm version remains `v1`.

## Verification

- Scoped compile passed. Scoped Ruff passed after formatting/import cleanup.
- Scoped mypy passed projection mapping/repository/fence, catalog port/repository and generator.
- Final scoped unit rerun passed 64 tests in 1.49 seconds: catalog repository, generator, Unicode word-boundary/compact hard-constraint regressions and admin route behavior. Scoped mypy passed 12 source files; compile and Ruff passed.
- The worker owner reported the combined real-PostgreSQL suite passing 37 cases in 18.07 seconds, including six cases in `test_catalog_projection_parity.py` and `test_catalog_publication_fence.py`. Coverage includes full search/filter/order/count/page parity, compact generation parity, micronutrient-only facet isolation, exact normalized-step/title/enrichment digests, selected dirty hydration, immediate withdrawal, direct publisher blocked by shared fence, one-SELECT summaries and bounded restart cursor.
- After the final generated timestamp-trigger snapshots were applied, `test_catalog_projection_publication.py` passed independently in 2.70 seconds. It verifies seed replacement, rank and admin image writes publish current typed query/macros/compact summaries without an intermediate commit, and preserve updated timestamps.
- Initial PostgreSQL execution found JSONB operator-precedence failure in catalog INSERT invalidation. Extracted JSONB operands now have explicit parentheses; the integrator refreshed the generated migration and verified upgrade/downgrade/upgrade locally. Fixture defects were corrected by reusing the canonical registry and binding JSON values.

## CPU evidence

`scripts/benchmarks/weekly_planner_selection.py --baseline <revision> --repeats 5` constructs synthetic canonical and compact inputs outside the timed region. Values are median process CPU milliseconds. Every repetition asserts identical ordered `(day,slot,recipe)` coordinates against the old source; selection digests are retained in [raw evidence](./catalog-selection-cpu-2026-10-03.json).

| Synthetic recipes | Baseline canonical | Optimized canonical | Optimized compact |
|---:|---:|---:|---:|
| 200 | 26.078 | 4.198 | 2.528 |
| 1,000 | 140.015 | 31.115 | 13.276 |
| 5,000 | 759.437 | 110.277 | 72.379 |
| 10,000 | 1,350.443 | 244.095 | 167.291 |

At 10,000 recipes, this fixture reduced selection CPU by about 87.6% using compact candidates. It excludes source loading, mapping, provider work, network RTT, auth and persistence. It does not establish deployed endpoint latency or concurrency capacity.

## Actual PostgreSQL query plans

`scripts/benchmarks/catalog_projection_explain.py` inserted 1,000 synthetic canonical recipes (900 active), rebuilt them through real conversion, analyzed source/projection tables, captured actual repository COUNT/page SQL with `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, and rolled back source writes. Database: disposable localhost PostgreSQL 14. [Raw plans](./catalog-projection-explain-2026-10-03.json) contain only synthetic query cases and database plan structure.

| Case | COUNT execution ms | Page execution ms |
|---|---:|---:|
| All active, LIMIT 20 | 0.457 | 1.081 |
| Cuisine subset | 0.290 | 0.563 |
| Selective substring, 10 matches | 0.293 | 0.330 |
| Cooking-time subset | 0.337 | 0.851 |
| Deep page, OFFSET 800 | 0.336 | 2.363 |

Plans used hash joins/sequential scans and approximately 240 shared-hit blocks. This small local distribution did not justify a new index; none was added. Production catalog skew, catalog growth, larger offsets, source-update fanout and Neon round trips require fresh EXPLAIN/load evidence before index or pool changes.

## Rollout gates and docs

All optimized flags default off. Initial projection backfill, complete active query/nutrition rows, canonical preference/Unicode parity and publication/reconciliation coverage gate compact cutover. Missing mandatory fields remain complete through authoritative fallback, rather than being silently omitted. Synchronous refresh covers the three standard application paths above. The requirement for synchronous complete projection publication across direct SQL and shared dependency writers is not yet achieved: those paths provide invalidation plus canonical fallback and durable repair. Sustained fallback can erase the expected performance gain; measure fanout, time to clean and fallback rate before release.

Existing database and architecture guides plus the catalog release runbook now document schema IDs `20261003042253928494`/`20261003102324493409`, five flags, worker/backfill commands, readiness SQL, exact current-week/GET compatibility and rollback preserving expanded tables/jobs/source data. Authenticated load instructions describe distinct account/token lanes, preseeded IDs/revisions, read-only `auto_generate=false`, explicit dedicated-target/mutation opt-ins, separate live-provider cases and static idempotency replay limitations. Migration mechanics and synthetic tests remain distinct from deployed-SHA verification, staging request/provider evidence, device persistence and concurrent scale/load gates.

Docs impact: major.

Unresolved release gates: synchronous complete projection coverage for all direct/shared dependency publishers; deployed catalog distribution, cold/warm endpoint p95/p99, bytes/query counts with real RTT, update fanout, fallback frequency, lock duration, worker queue aging and target concurrency. Authenticated staging/live-provider load requires credentials and fixtures. No unresolved questions in the catalog owner scope.
