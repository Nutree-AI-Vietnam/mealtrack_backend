# Weekly meal-plan backend optimization — integrated local results

Updated: 2026-10-04. PR branch: `feature/weekly-meal-plan-backend-optimization`, reconciled with `delivery` through `08477c24` (including the 21-slot breakfast/lunch/dinner contract and two-slot repair migration). Isolated worktree preserves unrelated checkout changes. No deployment or production migration was run.

## Delivered ordered batches

1. Bounded privacy-safe instrumentation, released GET/owner/calorie contracts, actual QueuePool/NullPool configuration and acquisition wait.
2. Two-SELECT plan reads without catalog/pantry graphs, parent-before-slot mutation locks, deduplicated selected-recipe validation, authoritative missing handling and plan reuse for grocery counts.
3. Independent versioned typed catalog facets/links/summaries, complete SQL filter/order/count pages, canonical fallback, bounded backfill, standard synchronous publication hooks and race-safe compute-before-write generation.
4. Dedicated OpenAI purpose/client policy, hard-eligible named/slot-covered40-candidate windows, at-most-one widening, one shared transient retry, aggregate30/25second deadlines, selected grocery hydration and localized compact proposals without auto-apply.
5. SQL-authoritative atomic source/TRUNCATE preparation producers, fenced renewable leases/global worker admission, pure typed computation, resulting-facet USDA publication, persisted translations/micro provenance, provider-free enabled GETs and reserved worker/backfill processes.
6. Unit/PostgreSQL concurrency/degradation/recovery, migration/replay/backfill/rollback procedures, authenticated eight-group load harness and release gate documentation.

## Verification and limits

| Check | Result | Evidence boundary |
|---|---|---|
| CI-aligned unit suite |3452 passed,57 warnings;79.50% coverage (required65%)|Project .venv Python3.13; fixtures/port doubles, no live-provider quality claim|
| Full PostgreSQL suite |40 passed in16.79s|Re-run after merging latest delivery; disposable localhost PostgreSQL14; real SQL/locking/leases/commits, no Neon/load proof|
| Post-final type-only regression |18 passed|Recipe cache and persisted localization|
| Changed Python Ruff/format + compile |Passed|Only task files formatted; unrelated edits preserved|
| Import contracts |4 kept,0 broken|New worker package included in import graph|
| Architecture suite |20 passed,3 pre-existing failures|All offenders present in HEAD; route commits/admin repository allowlist and stale111<=46 services assertion|
| Global mypy |1048 errors/160 files; HEAD1051/162|Normalized comparison finds0 introduced error instances and3 removed; not globally green|
| Focused typing |New preparation/policy/presentation17 files, extra pool/root3 files, catalog12 files passed|Scoped checks, not a substitute for global result|
| Migration rehearsal |Breakfast, mixed-version slot repair, projection, and preparation chain applied from base; optimization migrations down/down/up preserve 21 slots, plan/pantry/flags/daylines|Dedicated database; PostgreSQL 14 needed a local compatibility function for an older MySQL-era migration; schema contraction removes derived jobs/results, never user source state|
| Reviewer |No remaining reviewed local code blocker|Pending release/capacity evidence stays open|

Unit coverage tool's final gate reported79.51%; terminal line rounding may differ. Test warnings concern existing framework deprecations.

## Measured local improvements

- A fresh shared/diverse 21-slot plan read issues two core SELECTs and hydrates neither catalog nor pantry. The pre-optimization 14-slot fixture issued eight SELECTs. Compact summaries add1 query on complete projections, separately tested. Endpoint auth/profile/budget/timezone/count/localization overhead and bytes were not benchmarked.
- Historical synthetic14-slot selection median CPU:1350.443ms HEAD canonical→244.095ms optimized canonical→167.291ms compact (87.6% reduction), five repetitions with identical coordinates. This algorithm-only result predates breakfast integration and is not a 21-slot or endpoint latency claim. Construction, SQL/mapping/provider/network/persistence excluded. See [catalog report](./catalog-projections-2026-10-03.md) and linked raw JSON.
- Actual PostgreSQL COUNT/page EXPLAIN on1000 synthetic recipes(900active):COUNT0.290–0.457ms,page0.330–2.363ms including offset800. Hash/sequential scans; no extra performance index justified or added. Integrity uniqueness constraints are required data guarantees.
- Real one-connection pool test measures waiting checkout >=25ms while another session holds its sole slot, then completes; size1/overflow0 remain unchanged. This verifies measurement plumbing, not throughput.
- Proposed endpoint/worker/provider budgets remain tunable targets. No measured deployed p50/p95/p99, payload reduction, maximum concurrency or provider savings is asserted.

## Migration/configuration/backfill/rollback requirements

Generated through the repository CLI:
- `20261003042253928494` adds publication/versioned catalog projections and source locks/invalidation.
- `20261003102324493409` adds durable jobs/translations, nullable micro provenance and atomic source/TRUNCATE producers. Immutable downgrade restores old producers before dropping job storage.

Use the [release runbook](../../docs/runbooks/meal-catalog-release.md) for exact commands, readiness SQL, bounded cursors, worker start, dedicated fixture load and rollback order. [.env.example](../../.env.example) now documents five false cutover flags, per-process interactive capacity/admission and blank dedicated worker/projection URL keys. Never enable flags before applying schema and preparing active projections. Backfill exact locale/facet readiness; old estimates without provenance stay pending.

Worker usesSQL polling, default2 lanes/global4 claims, lease120s/provider60s and pool lanes+1 withoverflow0. These defaults require measured deployment combined DB/provider allocation. Interactive default2/process requires fixed workers×replicas accounting; distributed leased interactive admission precedes autoscaling. SDK planner/worker retries are0; job retries are finite and observable.

Rollback flags first, retain expanded schema/source/jobs during application rollback, quiesce the worker before optional schema contraction. New reads ignore unversioned Redis keys; Redis is optional and total cache work is bounded. GET defaults remain compatible; no old-client rollout is assumed complete.

## Remaining release gates and exact implementation limitation

- Standard seed/import, rank and admin image publishers refresh projections synchronously. Direct SQL and broad food/reference/conversion/allergen writers synchronously invalidate and enqueue durable repair, with exact canonical fallback until reconciliation. Complete synchronous projection refresh across all those writers remains unimplemented; measure fanout/fallback rate and close this gate before claiming sustained SQL-only optimized browsing under edits.
- Existing global architecture/typing/lint debt is not globally repaired by this focused change. Changed-file checks pass; CI/release owners must resolve or explicitly own existing red gates before deployment.
- Pin deployed revisions and actual pool/provider/worker/replica/region configuration; run representative Neon/PgBouncer EXPLAIN, bytes/locks/checkouts, cold/warm endpoint percentiles and isolated50-user10-minute load with dedicated identities/fixtures.
- Live OpenAI usefulness/locale/token/prompt-cache evaluation, provider-limits failures and fixed total interactive allocation remain unverified. Offline safety fixtures cannot establish model quality.
- Deploy worker only after schema, reconcile/backfill all required locales, measure queue age/readiness/failure rate and confirm rollback/worker stop procedures on staging.
- Authenticated staging canaries and supported-client click/current-week/read/fresh-screen reopen/log/pantry/Undo device checks remain open. Existing mobile release-plan gates are unchanged.

An unrelated untracked catalog-sync script appeared during the final pass and received formatter/import-order edits inadvertently. Its behavior was unchanged and it was never run; it is excluded from the task manifest/checks. It contains hardcoded database credentials; rotate them and keep that file out of commits. No credential values are copied into these reports.

No production migration, mutation/load test or deployment ran. Docs impact: major. Active architecture/database/runbook and this plan carry progress; no active roadmap/changelog exists, archived docs left untouched.

## Changed-file manifest

Task source/tests/scripts/config/docs below; all plan phases/progress, safe benchmark artifacts and scoped reports are also updated/added.

- [.env.example](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/.env.example)
- [docs/database-guide.md](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/database-guide.md)
- [docs/runbooks/meal-catalog-release.md](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/runbooks/meal-catalog-release.md)
- [docs/system-architecture.md](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/system-architecture.md)
- [migrations/versions/20261003042253928494_add_versioned_catalog_planner_.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/migrations/versions/20261003042253928494_add_versioned_catalog_planner_.py)
- [migrations/versions/20261003102324493409_add_durable_catalog_preparation_jobs.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/migrations/versions/20261003102324493409_add_durable_catalog_preparation_jobs.py)
- [scripts/backfill_catalog_preparation.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/scripts/backfill_catalog_preparation.py)
- [scripts/benchmarks/catalog_projection_explain.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/scripts/benchmarks/catalog_projection_explain.py)
- [scripts/benchmarks/weekly_planner_load.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/scripts/benchmarks/weekly_planner_load.py)
- [scripts/benchmarks/weekly_planner_selection.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/scripts/benchmarks/weekly_planner_selection.py)
- [scripts/catalog_preparation_worker.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/scripts/catalog_preparation_worker.py)
- [scripts/development/rebuild_catalog_projections.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/scripts/development/rebuild_catalog_projections.py)
- [src/api/base_dependencies.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/base_dependencies.py)
- [src/api/dependencies/auth.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/dependencies/auth.py)
- [src/api/middleware/request_logger.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/middleware/request_logger.py)
- [src/api/routes/v1/meal_plans.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/routes/v1/meal_plans.py)
- [src/app/commands/meal_planner/ai_adjust_meal_plan_command.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/commands/meal_planner/ai_adjust_meal_plan_command.py)
- [src/app/handlers/query_handlers/meal_planner/weekly_meal_planner_query_handlers.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/query_handlers/meal_planner/weekly_meal_planner_query_handlers.py)
- [src/app/queries/meal_planner/get_weekly_groceries_query.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/queries/meal_planner/get_weekly_groceries_query.py)
- [src/app/services/catalog_meal_response_localizer.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_meal_response_localizer.py)
- [src/app/services/catalog_micronutrient_computer.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_micronutrient_computer.py)
- [src/app/services/catalog_persisted_presentation_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_persisted_presentation_service.py)
- [src/app/services/catalog_preparation_computer.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_preparation_computer.py)
- [src/app/services/catalog_recipe_micronutrient_enrichment_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_recipe_micronutrient_enrichment_service.py)
- [src/app/services/planner_presentation_copy.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/planner_presentation_copy.py)
- [src/app/services/weekly_grocery_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_grocery_service.py)
- [src/app/services/weekly_meal_logging_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_meal_logging_service.py)
- [src/app/services/weekly_meal_plan_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_meal_plan_service.py)
- [src/app/services/weekly_recipe_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_recipe_service.py)
- [src/bootstrap/catalog_preparation.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/bootstrap/catalog_preparation.py)
- [src/domain/model/ai/model_purpose.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/model/ai/model_purpose.py)
- [src/domain/model/meal_recommendation/catalog_recipe.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/model/meal_recommendation/catalog_recipe.py)
- [src/domain/model/meal_recommendation/catalog_selection_features.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/model/meal_recommendation/catalog_selection_features.py)
- [src/domain/model/weekly_meal_planner/ai_proposal.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/model/weekly_meal_planner/ai_proposal.py)
- [src/domain/ports/async_unit_of_work_port.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/ports/async_unit_of_work_port.py)
- [src/domain/ports/catalog_preparation_port.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/ports/catalog_preparation_port.py)
- [src/domain/ports/catalog_recipe_repository_port.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/ports/catalog_recipe_repository_port.py)
- [src/domain/ports/weekly_meal_plan_adjustment_provider_port.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/ports/weekly_meal_plan_adjustment_provider_port.py)
- [src/domain/services/weekly_meal_planner/weekly_plan_generation_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/services/weekly_meal_planner/weekly_plan_generation_service.py)
- [src/infra/adapters/meal_generation_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/adapters/meal_generation_service.py)
- [src/infra/adapters/weekly_meal_plan_adjustment_provider.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/adapters/weekly_meal_plan_adjustment_provider.py)
- [src/infra/database/catalog_publication_triggers.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/catalog_publication_triggers.py)
- [src/infra/database/config_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/config_async.py)
- [src/infra/database/models/__init__.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/__init__.py)
- [src/infra/database/models/meal_recommendation/__init__.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/meal_recommendation/__init__.py)
- [src/infra/database/models/meal_recommendation/catalog_micronutrient_enrichment.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/meal_recommendation/catalog_micronutrient_enrichment.py)
- [src/infra/database/models/meal_recommendation/catalog_preparation.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/meal_recommendation/catalog_preparation.py)
- [src/infra/database/models/meal_recommendation/catalog_projection.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/meal_recommendation/catalog_projection.py)
- [src/infra/database/planner_pool.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/planner_pool.py)
- [src/infra/database/uow_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/uow_async.py)
- [src/infra/observability/planner_metrics.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/observability/planner_metrics.py)
- [src/infra/repositories/admin_meal_catalog_repository_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/admin_meal_catalog_repository_async.py)
- [src/infra/repositories/catalog_preparation_claims.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_preparation_claims.py)
- [src/infra/repositories/catalog_preparation_publisher.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_preparation_publisher.py)
- [src/infra/repositories/catalog_preparation_reads.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_preparation_reads.py)
- [src/infra/repositories/catalog_preparation_repository.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_preparation_repository.py)
- [src/infra/repositories/catalog_projection_builder.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_projection_builder.py)
- [src/infra/repositories/catalog_projection_filters.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_projection_filters.py)
- [src/infra/repositories/catalog_projection_legacy_page.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_projection_legacy_page.py)
- [src/infra/repositories/catalog_projection_mapper.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_projection_mapper.py)
- [src/infra/repositories/catalog_projection_rebuilder.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_projection_rebuilder.py)
- [src/infra/repositories/catalog_projection_repository.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_projection_repository.py)
- [src/infra/repositories/catalog_publication_fence.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_publication_fence.py)
- [src/infra/repositories/catalog_recipe_repository_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_recipe_repository_async.py)
- [src/infra/repositories/meal_write_operation_repository_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/meal_write_operation_repository_async.py)
- [src/infra/repositories/weekly_meal_plan_repository_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/weekly_meal_plan_repository_async.py)
- [src/infra/services/ai/ai_model_manager.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/ai/ai_model_manager.py)
- [src/infra/services/ai/planner_generation_policy.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/ai/planner_generation_policy.py)
- [src/infra/services/ai/providers/openai_provider.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/services/ai/providers/openai_provider.py)
- [src/infra/workers/__init__.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/workers/__init__.py)
- [src/infra/workers/catalog_preparation_metrics.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/workers/catalog_preparation_metrics.py)
- [src/infra/workers/catalog_preparation_runtime.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/workers/catalog_preparation_runtime.py)
- [src/infra/workers/catalog_preparation_worker.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/workers/catalog_preparation_worker.py)
- [src/planner_observability.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/planner_observability.py)
- [src/planner_request_policy.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/planner_request_policy.py)
- [tests/integration/postgres/catalog_preparation_fixtures.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/catalog_preparation_fixtures.py)
- [tests/integration/postgres/catalog_projection_fixtures.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/catalog_projection_fixtures.py)
- [tests/integration/postgres/test_catalog_preparation_publication.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_catalog_preparation_publication.py)
- [tests/integration/postgres/test_catalog_preparation_recovery.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_catalog_preparation_recovery.py)
- [tests/integration/postgres/test_catalog_preparation_worker.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_catalog_preparation_worker.py)
- [tests/integration/postgres/test_catalog_projection_parity.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_catalog_projection_parity.py)
- [tests/integration/postgres/test_catalog_projection_publication.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_catalog_projection_publication.py)
- [tests/integration/postgres/test_catalog_publication_fence.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_catalog_publication_fence.py)
- [tests/integration/postgres/test_planner_migration_rollback.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_planner_migration_rollback.py)
- [tests/integration/postgres/test_planner_pool_wait.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_planner_pool_wait.py)
- [tests/integration/postgres/test_weekly_generation_transaction.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_weekly_generation_transaction.py)
- [tests/integration/postgres/test_weekly_planner_optimization.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/integration/postgres/test_weekly_planner_optimization.py)
- [tests/unit/api/test_weekly_meal_planner_contract.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/api/test_weekly_meal_planner_contract.py)
- [tests/unit/app/services/test_catalog_meal_response_localizer.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_catalog_meal_response_localizer.py)
- [tests/unit/app/services/test_catalog_preparation_computer.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_catalog_preparation_computer.py)
- [tests/unit/app/services/test_weekly_grocery_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_weekly_grocery_service.py)
- [tests/unit/app/services/test_weekly_meal_plan_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_weekly_meal_plan_service.py)
- [tests/unit/app/services/test_weekly_recipe_service.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_weekly_recipe_service.py)
- [tests/unit/domain/services/weekly_meal_planner/test_catalog_selection_features.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/domain/services/weekly_meal_planner/test_catalog_selection_features.py)
- [tests/unit/infra/adapters/test_weekly_meal_plan_adjustment_provider.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/adapters/test_weekly_meal_plan_adjustment_provider.py)
- [tests/unit/infra/database/test_config_async.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/database/test_config_async.py)
- [tests/unit/infra/observability/test_planner_metrics.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/observability/test_planner_metrics.py)
- [tests/unit/infra/repositories/test_weekly_meal_plan_repository_loaders.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/repositories/test_weekly_meal_plan_repository_loaders.py)
- [tests/unit/infra/services/ai/providers/test_openai_provider.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/services/ai/providers/test_openai_provider.py)
- [tests/unit/infra/services/ai/test_planner_generation_policy.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/services/ai/test_planner_generation_policy.py)
- [tests/unit/infra/services/ai/test_planner_routing.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/services/ai/test_planner_routing.py)
- [tests/unit/test_weekly_planner_load_guards.py](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/test_weekly_planner_load_guards.py)
