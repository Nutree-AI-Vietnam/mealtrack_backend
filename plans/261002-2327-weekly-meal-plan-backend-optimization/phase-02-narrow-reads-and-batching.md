---
phase: 2
title: "Narrow Reads And Batching"
status: in_progress
effort: ""
priority: P1
dependencies: [1]
---

# Phase 2: Narrow Reads And Batching

## Overview

Lean reads, selected validation and batching implemented; scoped/unit/PostgreSQL checks passed. Live endpoint bytes/RTT remain pending.

## Context Links

- [Solution](./solution-document.md), [baseline](./phase-01-baseline-and-contract-guards.md), [catalog projection phase](./phase-03-catalog-projections-and-generation.md).
- [Performance review](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/reports/meal-plan-performance-review-2026-10-02.md), [DB/API rules](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/standards/db-api.md).

## Key Insights

- Basic plan load produced eight SELECTs in the review's SQLite setup: slot→catalog joins/select-in relationships are mapped then discarded.
- One-slot swaps currently call full `list_active_meals()`. Grocery writes already prefetch state and flush once; do not assume per-item SELECTs.
- Grocery count reloads the plan; batched recipe misses can fall back to repeated `get_meal()` calls.

## Requirements

- Preserve domain mapping, owner checks, expected revisions, slot/log races, missing-recipe behavior, hard constraints, and response shapes.
- Target plan + slots + selected compact summaries at ≤3 core SELECTs, excluding auth/budget/groceries/cache/localization; verify this on PostgreSQL rather than asserting all-route count.
- Never parallelize calls on one `AsyncSession`; use batch SQL or sequential awaits. Bound IDs and deduplicate before repository calls.

## Architecture

Separate read projections from mutation loaders. Plan reads load slot identifiers/state and omit unused pantry/catalog graphs; summary/ingredient reads explicitly fetch only selected recipe IDs. Reuse the owner-validated plan inside grocery projection through application query/service data, without passing ORM/session objects into domain or between UoWs.

## Related Code Files

- Modify: [plan repository loaders](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/weekly_meal_plan_repository_async.py:340), [slot relationship](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/weekly_meal_planner/weekly_meal_plan_slot.py:44), [plan repository port](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/domain/ports/weekly_meal_plan_repository_port.py).
- Modify: [selected validation](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_meal_plan_service.py:151), [recipe summaries](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_recipe_service.py:130), [batched catalog repository](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_recipe_repository_async.py:183).
- Modify through integrator: [grocery calculation](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_grocery_service.py:65), [grocery query handler](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/handlers/query_handlers/meal_planner/weekly_meal_planner_query_handlers.py:80), [plan response](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/routes/v1/meal_plans.py:639).
- Extend: [plan service tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_weekly_meal_plan_service.py), [grocery tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_weekly_grocery_service.py), [catalog repository tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/repositories/test_catalog_recipe_repository_async.py).
- Create: focused PostgreSQL SQL/row/bytes regression coverage under `tests/integration/postgres/`. Delete: none; migration only if measured schema needs emerge.

## Implementation Steps

1. Add loader/query assertions for fresh sessions, shared/diverse recipes, empty/removed catalog relations, and lock reads before changing options.
2. Override slot catalog loading on plan/slot read and lock paths; omit pantry from paths that do not consume it. Trace callers before changing global relationship defaults.
3. Add a selected compact-summary repository projection; keep explicit full-ingredient loader for groceries/logging. Count returned rows and bytes as well as statements.
4. For swaps fetch replacement IDs only; if hard preferences change, validate current unlogged recipe IDs plus replacements. Preserve null clears, unsuitable/inactive recipes, logged-slot rejection, and exact error codes.
5. Pass the already owner-validated plan into count/grocery calculation through an application-owned operation; fetch required recipes/pantry/interactions once in its UoW.
6. Deduplicate missing IDs and return existing bounded missing behavior; remove fallback per-ID reads where the batch result is authoritative. Cover recipes deleted after persistence.
7. Retain existing prefetched pantry writes/one flush. Benchmark bulk DML only for real high-update batches and adopt only with state/replay parity evidence.
8. Compile changed files, run route/service/repository/grocery/logging regressions, and compare real PostgreSQL core SELECTs/bytes/latency to phase 1.

## Todo List

- [x] Split lean read and explicit mutation loaders; verify ownership and locking.
- [x] Bound swap validation and recipe missing handling to selected IDs.
- [x] Reuse plan/count data and remove duplicate catalog/pantry work.
- [ ] Publish PostgreSQL SQL/bytes comparison and focused regression results.

## Success Criteria

- [x] Basic plan core read uses ≤3 SELECTs under the documented PostgreSQL fixture; full endpoint overhead is reported separately.
- [x] Single-slot validation cost scales with changed IDs; hard preference changes cover every unlogged selected recipe.
- [ ] Full/zero/partial pantry, flags, Undo, repeated logging, replay, stale revision, and unauthorized-owner behavior persist on fresh reads.
- [x] No lazy-load surprise, AsyncSession concurrency, or additional per-missing-recipe query is introduced.

## Risk Assessment and Security

- Narrow loaders can hide needed mutation state: protect update/log/lock scenarios before removing relationships.
- Missing recipes must preserve nullable historical slots and published/nutrition constraints; never substitute unauthorized or unsafe recipes.
- Count reuse must preserve owner validation and coherent revision; do not cache user pantry state under catalog-only keys.

## Unresolved Measurements and Next Steps

- Unknown: PostgreSQL batch thresholds, row/byte reduction, p95 gains, and whether large grocery writes justify bulk DML.
- Phase 3 consumes the compact-summary and selected-validation interfaces. Integrator coordinates shared service edits and phase 6 regression gates.

## Verified Local Progress — 2026-10-03

- Fresh shared/diverse 14-slot plan reads issue two core SELECTs and hydrate neither catalog nor pantry. Same local fixture before optimization issued eight SELECTs; auth/profile/timezone/budget/presentation are outside this comparison.
- Swap hydration uses deduplicated requested IDs; changed hard preferences additionally validate all unlogged selected IDs. Missing active recipes do not trigger per-ID fallback queries.
- Grocery count reuses the owned domain plan. Fresh PostgreSQL full/zero/partial pantry, flags/day lines/Undo and swap/log/revision cases pass.

Evidence: [integrated report](../reports/implementation-2026-10-03-weekly-planner-optimization.md). Open composite acceptance criteria stay unchecked; local implementation does not establish release completion.
