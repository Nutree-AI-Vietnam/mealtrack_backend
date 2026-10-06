---
phase: 2
title: "Repair write and slot flows"
status: in_progress
priority: P1
effort: "1d"
dependencies: [1]
---

# Phase 2: Repair write and slot flows

## Overview

Repair legacy `v1` plans written after the earlier migration. Keep existing API shapes and preserve every assigned or logged meal, pantry item, and plan revision contract.

## Related Code Files

- Backend: a CLI-generated Alembic data migration and focused migration/PostgreSQL tests.
- Existing app/backend contracts: `src/domain/model/weekly_meal_planner/weekly_meal_plan.py`, `src/infra/repositories/weekly_meal_plan_repository_async.py`, `lib/features/recipes/domain/weekly_meal_slots.dart`.

## Implementation Steps

1. Verify the production writer revision and deploy the current three-slot writer before data repair. Confirmed 2026-10-06: `https://api.nutreeai.com` (`nutree-backend`) still publishes `slot_index` maximum 1, 14-slot updates, and no `grocery-days` route. Three draft `v1` plans for week 2026-10-05 still have 14 slots. Alembic head is `20261005022758771114`.
2. Generate a new Alembic revision through the repository CLI. Repair only exact draft `v1` seven-day `{0,1}` plans: retain lunch/dinner slot IDs and logged links, shift 1→2 and 0→1, insert empty breakfast 0, increment revision. Skip ambiguous shapes. Give the data repair a valid safe downgrade. Done in `20261006115245714931`.
3. The repository repairs that same shape on read and on meal, pantry, and day-note writes, so the three-slot server can serve these plans before the migration runs. A read bumps revision. A write keeps the revision the client loaded, then applies the edit on the shifted coordinates.
4. Add a PostgreSQL regression for plans created after the earlier migration, including repeated-run behavior and preservation of assigned and logged meals. Domain and repository unit tests cover the shape check and the in-memory shift.
5. Leave breakfast empty. Do not overwrite lunch or dinner.

## Success Criteria

- [ ] All three reported actions succeed for valid inputs and show actionable errors for invalid/stale inputs.
- [ ] Backend ownership, calories, allergies, revision, idempotency, logged-slot and pantry contracts remain intact.
- [ ] Focused regressions fail on prior behavior and pass after repair.
