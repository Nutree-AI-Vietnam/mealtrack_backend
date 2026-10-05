---
phase: 3
title: "Catalog Projections And Generation"
status: in_progress
effort: ""
priority: P1
dependencies: [2]
---

# Phase 3: Catalog Projections And Generation

## Overview

Versioned projections, SQL browsing, publication fences and short generation implemented; local parity/race checks passed. Direct/shared writer performance cutover remains gated.

## Context Links

- [Solution](./solution-document.md), [read interfaces](./phase-02-narrow-reads-and-batching.md), [durable preparation](./phase-05-durable-catalog-preparation.md).
- [Performance review](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/reports/meal-plan-performance-review-2026-10-02.md), [schema rules](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/standards/db-api.md).

## Key Insights

- Existing `CatalogMealSnapshotService` caches full domain objects per process with TTL/singleflight; reuse its patterns, not its payload as a compact durable projection.
- Current revision aggregates catalog and food-reference timestamps; projection dependencies must also cover nutrients/conversion rules and relevant eligibility/text changes.
- Existing indexes use raw cuisine/name, while queries can use `lower(...)`; usefulness requires actual query-plan evidence.

## Requirements

- Typed per-recipe selection/list columns carry authoritative macros/calories, suitability, publication/nutrition state, normalized filters, and dependency revision; they remain rebuildable read models.
- Mandatory filter/order/count/eligibility fields refresh synchronously with published changes. Dirty macro/ingredient payloads rebuild only for selected page/plan IDs; never omit dirty rows or change list totals. Refresh dirty selection candidates before ranking; retain authoritative legacy selection during initial backfill. Withdrawals take effect immediately.
- Version actual dependency facets, not coarse food-reference timestamps: micronutrient-only changes do not dirty selection macros, and estimate persistence must not invalidate itself.
- Preserve deterministic seed/tie order, casefold search, allergen registry/fail-closed constraints, diets/dislikes, slot suitability, logged slots, and pagination totals.
- Generation must protect missing-week concurrency and atomically complete the existing operation ledger with the plan; do not split reservation lifetime blindly.

## Architecture

Publishers lock the publication-revision row FOR UPDATE before relevant source changes and refresh mandatory query fields in that transaction. Final generation takes FOR SHARE on that row, then stable owner/week advisory lock, then plan/ordered slot locks; revalidate source, plan and slot versions and atomically persist/complete idempotency. Compute occurs outside this critical transaction. SQL filters/count/page remain complete while bounded selected-ID payload hydration handles dirty data; stable features use dependency-facet versions.

## Related Code Files

- Modify: [catalog port/revision](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/ports/catalog_recipe_repository_port.py:86), [catalog SQL/mapping](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_recipe_repository_async.py:82), [recipe filters](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_recipe_service.py:66).
- Modify: [generator](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/services/weekly_meal_planner/weekly_plan_generation_service.py:33), [generation orchestration](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_meal_plan_service.py:72), [user/week lock](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/weekly_meal_plan_repository_async.py:39).
- Modify through integrator: [slot logging lock/version behavior](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_meal_logging_service.py), [slot lock/mark](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/weekly_meal_plan_repository_async.py:285); preserve public plan revision while adding ordered locks/internal slot-version fencing.
- Read/reuse: [snapshot patterns](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_meal_snapshot_service.py:33), [publication policy](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/services/weekly_meal_planner/recipe_publication.py), [catalog model/indexes](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/meal_recommendation/catalog_recipe.py:146).
- Modify through integrator: [central registry](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/__init__.py), [UoW repository wiring](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/uow_async.py:124).
- Extend: [generator tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/domain/services/weekly_meal_planner/test_weekly_plan_generation_service.py), [snapshot tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_catalog_meal_snapshot_service.py).
- Create: focused typed projection/model/rebuilder modules in existing catalog layers, registry import, generated migration, and PostgreSQL parity/concurrency tests. Delete: none.

## Implementation Steps

1. Specify dependency-facet digests and typed projection contracts; cover ingredient/food-reference/macro/conversion/publication/allergen/title edits, deletion and retry. Avoid micronutrient-only invalidation of macro selection and self-invalidating enrichment results.
2. Generate schema via migration CLI; register models, implement downgrade, bounded idempotent backfill, dual reads, and old-loader fallback while projections warm.
3. Add SQL list filtering, stable sorting, COUNT and pagination with parity tests against current Python behavior including Unicode casefold and hard allergy constraints.
4. Capture representative PostgreSQL EXPLAIN before conditional partial/expression `lower(cuisine)`, popularity/`lower(name)` or `pg_trgm` indexes; create only measured useful indexes via CLI.
5. Adapt generation to compact candidates and precomputed stable checks while preserving authoritative calorie/conversion parity; fetch full selected ingredients only for projections/logging needing them.
6. Check replay and existing confirmed plan before full catalog computation. Trace operation-ledger transaction/claim rules; design preflight/final reserve ordering without durable orphan claims.
7. Compute outside the critical write; take publication FOR SHARE, then stable user/week advisory lock including absent rows, reserve/recheck ledger and lock plan/relevant slots in coordinate order. Revalidate plan/source/slot versions and logged state. All canonical publishers take publication FOR UPDATE before mutation; logging participates in plan→slot locking and internal slot-version fencing without silently changing public plan revision.
8. Permit at most one catalog-change selection restart outside the lock, then retryable conflict; plan changes preserve existing conflict behavior. Preserve uniqueness fallback, replay/logged recipes/cancellation rollback; re-measure CPU, event-loop lag, SQL bytes and lock duration.
9. Compile and pass source/projection parity, list/search/nutrition and real PostgreSQL concurrency regressions; publish interfaces for phases 4/5.

## Todo List

- [x] Define dependency/version invalidation and compact typed rows.
- [x] Backfill behind flags with legacy fallback; prove SQL filter/count parity.
- [x] Remove repeated stable selection work and shorten final-write lock scope.
- [x] Verify missing-week races and atomic replay; record benchmark/index evidence.

## Success Criteria

- [ ] Every projected value matches canonical source/conversion output and invalidates on all declared dependencies.
- [ ] Search/filter/order/count pagination parity holds, including allergies and Unicode; indexes have actual-query EXPLAIN evidence.
- [x] Same/different-key generation races create one owner/week plan with correct replay/conflict, no orphan ledger record, and preserved logged slots.
- [ ] Dirty browsing retains exact totals/pages; generation versus publication/withdrawal and logging cannot pass stale validation then commit.
- [x] Generation remains current-week, deterministic, and LLM-free; old GET clients retain documented behavior.

## Risk Assessment and Security

- Stale projection can select unsafe/unpublished recipes: validate selected source versions/eligibility at final commit and fail closed on hard constraints.
- Advisory-key collisions serialize extra work: use a stable namespaced hash and retain unique owner/week constraint; never Python's process-random hash.
- Moving compute outside UoW can break replay/concurrency: prove ledger and cancellation cases before cutover; do not hold provider calls in writes.

## Unresolved Measurements and Next Steps

- Unknown: dependency update volume, projection size/backfill time, live EXPLAIN benefit, catalog-change retry rate, and need for measured CPU offload.
- Phase 4 uses eligible compact candidates; phase 5 consumes invalidation/version contracts. Integrator serializes edits to shared services/wiring.

## Verified Local Progress — 2026-10-03

- Typed independent facets, normalized allergen links, SQL exact counts/pages and clean compact candidates are gated by complete source freshness. Dirty query fields preserve complete canonical fallback; dirty nutrition hydrates selected page IDs.
- Standard seed/import, rank and admin image writes rebuild before commit under the exclusive fence. Direct SQL and broad shared dependency writers invalidate and durably reconcile; complete synchronous refresh across those writers is not implemented and remains an explicit cutover gate.
- Same/different-key missing-week generation commits one plan/completed ledger, cancellation leaves no reservation, and snapshot/facet/revision rechecks preserve logged slots. Publication shared/exclusive fences and slot locking are tested.
- Local synthetic 10k selection CPU median: 1350.443 ms canonical HEAD → 244.095 ms optimized canonical → 167.291 ms compact; output coordinates match. PostgreSQL 1k EXPLAIN did not justify extra indexes.

Evidence: [integrated report](../reports/implementation-2026-10-03-weekly-planner-optimization.md). Open composite acceptance criteria stay unchecked; local implementation does not establish release completion.
