---
phase: 3
title: "Weekly Plan Persistence And Generation"
status: pending
priority: P1
effort: "5-8d"
dependencies: [1, 2]
mode: deep
---

# Phase 3: Weekly Plan Persistence And Generation

## Overview

Add the durable weekly aggregate and deterministic 14-slot generator. This
phase owns persistence/domain/application behavior; HTTP routes and grocery
presentation follow in Phase 4.

## Context Links

- Existing deterministic primitives: `src/domain/services/meal_recommendation/`
- Snapshot: `src/app/services/catalog_meal_snapshot_service.py`
- Budget query: `src/app/queries/get_weekly_budget_query.py`
- UoW: `src/infra/database/uow_async.py`
- Migration policy: `AGENTS.md`, `scripts/development/migrate.sh`

## Requirements

- Add `weekly_meal_plans` with owner, Monday week start, `draft|confirmed`,
  people count 1..6, bounded preferences JSON snapshot, version/timestamps,
  and a unique current owner/week constraint.
- Add `weekly_meal_plan_slots` with exactly one row per `(plan, day_index,
  slot_index)`, nullable catalog meal, logged meal reference, execution state,
  and update/version data for safe mutations.
- Add `weekly_meal_plan_pantry_items` keyed by plan and canonical ingredient,
  with bounded custom amount and `bought|owned` stock state.
- Generation reads the user timezone and current adjusted weekly target through
  `GetWeeklyBudgetQuery`; `remaining_days` includes today. It uses the shared
  immutable snapshot/scoring primitives but emits 14 lunch/dinner slots.
- Generation is deterministic for the same catalog revision, user, week,
  target, preferences, and algorithm version. No LLM, web lookup, Redis result
  cache, or client nutrition input.
- All commands use owner scope, transaction locks, idempotency fingerprints,
  and the existing `durable_write_records` claim/replay service; do not create
  a second generic operation ledger. Use a typed conflict when the approved
  lifecycle disallows mutation.

## Architecture

Introduce a `weekly_meal_planner` domain/application package beside—not inside—
the three-day recommendation package. A pure weekly generator allocates lunch
and dinner target calories, filters eligible catalog meals, applies diet/time/
cuisine/dislike policy, and chooses stable IDs/tie-breakers. A repository
persists the aggregate plus all empty slots atomically. The `meal_recommendations`
tables remain unchanged and are not repurposed for weekly draft state.

## Related Code Files

| Action | Files |
|---|---|
| Create | `src/domain/model/weekly_meal_planner/weekly_meal_plan.py`, `weekly_meal_plan_preferences.py` |
| Create | `src/domain/services/weekly_meal_planner/weekly_plan_generation_service.py` |
| Create | `src/domain/ports/weekly_meal_plan_repository_port.py` |
| Create | `src/infra/database/models/weekly_meal_planner/weekly_meal_plan.py`, `weekly_meal_plan_slot.py`, `weekly_meal_plan_pantry_item.py` |
| Create | `src/infra/repositories/weekly_meal_plan_repository_async.py` |
| Reuse | `src/infra/services/durable_write_service.py`, `src/infra/database/models/durable_write_record.py` |
| Create | `src/app/commands/meal_planner/generate_weekly_meal_plan_command.py`, `update_weekly_meal_plan_command.py` |
| Create | matching handlers, queries, and package exports under `src/app/` |
| Modify | `src/infra/database/uow_async.py`, `src/api/dependencies/event_bus.py`, model exports |
| Create | generated migration via `./scripts/development/migrate.sh generate "create_weekly_meal_planner_tables"` |
| Add | domain, repository, handler, migration, and concurrency tests |

## Implementation Steps

1. Define value objects/enums for week, slot, status, preferences, and
   idempotency fingerprint with strict bounds and no outer-layer imports.
2. Generate the migration with the required foreign keys/checks/indexes and
   valid downgrade. Add a database constraint or transaction invariant that
   prevents duplicate slot coordinates and enforces owner-scoped links.
3. Implement repository methods for current-week read, draft creation/update,
   slot locking, idempotent generation, version checks, and atomic confirmation.
4. Implement pure weekly generation using the catalog snapshot and existing
   score/allocation services. Preserve safe slots when a filter yields no
   candidates; return a typed insufficiency only when the plan cannot be built.
5. Wire commands/queries through the event bus and register fresh UoW copies;
   never share request sessions or handlers across concurrent calls.

## Tests Before

- Lock existing catalog snapshot and scoring goldens before adding the weekly
  optimizer.
- Add aggregate tests proving no `meal_recommendations` rows are created by
  weekly generation.

## Tests After / Scenario Matrix

| Scenario | Expected evidence |
|---|---|
| Monday validation/default week | Correct timezone-local week |
| Empty initial plan | 14 rows, null recipe IDs, stable coordinates |
| Deterministic generation | Same inputs/revision produce same selections |
| Preference fallback | No matching candidate preserves slot or returns typed insufficiency |
| People count | Grocery multiplier only; recipe nutrition unchanged |
| Budget target | Weekly budget target is captured and used; today-inclusive days preserved |
| Concurrent generate | One owner/week write wins; loser replays or gets stable conflict |
| Owner isolation | Cross-user plan IDs never resolve |
| Draft/confirmed | State transition and forbidden mutation are atomic |
| Rollback | Partial slot failure leaves no plan or partial slots |

## Function / Interface Checklist

- Generator accepts immutable catalog/policy inputs and returns no ORM objects.
- Repository owns SQL locking, idempotency, and persistence; handler owns
  orchestration/error conversion.
- `GetCurrentWeeklyPlanQuery` returns an application projection, not a session-
  bound ORM graph.
- Weekly plan IDs cannot be confused with three-day recommendation batch IDs.

## Dependency Map

- Depends on Phases 1-2 and the existing catalog snapshot/ranking foundation.
- Blocks all plan, grocery, AI, and log routes.
- Phase 4 may consume projections only; it must not bypass repository invariants.

## Success Criteria

- [ ] Migration creates durable owner-scoped weekly state with 14-slot invariant.
- [ ] Generation is deterministic, budget-aware, preference-aware, and
  independent of LLM/Redis.
- [ ] Concurrent/idempotent command tests pass with no partial writes.

## Risk Assessment

The main risk is accidentally creating a second recommendation engine. Reuse
existing snapshot, nutrition, and scoring primitives; only the 7-day slot
shape and preference policy are new.

## Security Considerations

Every repository lookup must include `user_id` in the SQL predicate or owner
anchor. Never authorize from a client-provided plan/slot relationship alone.

## Next Steps

Expose the aggregate and derived groceries through owner-scoped API routes.
