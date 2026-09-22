---
phase: 1
title: "Contract Reconciliation And Product Gates"
status: pending
priority: P1
effort: "2-3d"
dependencies: []
mode: deep
---

# Phase 1: Contract Reconciliation And Product Gates

## Overview

Freeze the weekly planner contract against live backend behavior before any
schema or route implementation. Resolve naming, lifecycle, regeneration,
allergy, idempotency, localization, and nutrition-authority decisions.

## Context Links

- Handoff: `/Users/alexnguyen/.gemini/antigravity/brain/7d122d77-785b-4020-91bc-4ea4800d7d0c/api_design_weekly_meal_planner.md`
- Architecture: `docs/system-architecture.md`, `docs/cqrs-guide.md`
- API conventions: `docs/api-endpoints.md`
- Existing catalog route: `src/api/routes/v1/meal_catalog.py`
- Existing recommendation route: `src/api/routes/v1/meal_recommendations.py`
- Existing recommendation plan: `src/domain/model/meal_recommendation/`

## Requirements

- Canonical week is Monday through Sunday; validate `week_start_date` is a
  Monday and derive the default week from the authenticated user's timezone.
- Slot identity is `(day_index 0..6, slot_index 0 lunch / 1 dinner)`; every
  persisted plan owns all 14 slots, including empty slots.
- `draft → confirmed` is one-way unless the product explicitly chooses a
  supersession/reopen contract. Confirmed behavior must be reflected in every
  PATCH and AI path; pantry updates and diary logging remain allowed only if
  their separate invariants pass.
- Define idempotency headers, replay semantics, public error codes, owner
  authorization, pagination limits, request-size limits, and language fallback.
- Decide whether allergy matching is authoritative or disclosure-only. No plan
  may state that an allergy is safe without canonical allergen data and tests.

## Architecture

Create a contract matrix mapping each handoff endpoint to the existing
catalog/recommendation/meal path. `/v1/recipes` is a read alias backed by the
catalog service; `/v1/meal-plans` is a separate weekly aggregate. Commands and
queries remain application-layer objects; domain generation and grocery math
cannot import FastAPI, SQLAlchemy, OpenAI, or translation adapters.

## Related Code Files

| Action | Files |
|---|---|
| Read | `src/api/main.py`, `src/api/dependencies/event_bus.py`, `src/api/dependencies/auth.py` |
| Read | `src/app/queries/get_weekly_budget_query.py`, `src/app/services/catalog_meal_log_service.py` |
| Read | `src/domain/services/meal_recommendation/recipe_scoring_service.py`, `src/domain/services/weekly_budget_service.py` |
| Add | `tests/unit/api/test_weekly_meal_planner_contract.py` |
| Add | `plans/260921-0000-weekly-meal-planner-backend/reports/contract-matrix.md` |

## Implementation Steps

1. Inventory all existing catalog, recommendation, meal logging, translation,
   feature-flag, and weekly-budget callers; record exact reuse points.
2. Write the endpoint/DTO/error matrix for current plan, generate, patch,
   recipes, detail, groceries, pantry, AI proposal, and slot log.
3. Lock request limits and state transitions, including duplicate slot handling,
   empty slots, stale writes, confirmed-plan behavior, and idempotency replay.
4. Confirm the additive catalog-detail shape: extend `meal_catalog`, add
   `meal_catalog_steps`, and add ingredient categories; do not reuse normalized
   user-meal steps or duplicate recipe authority.
5. Record the allergy decision and mark any unimplementable handoff fields as
   explicit compatibility/deferred behavior before schema work.

## Tests Before

| Scenario | Expected evidence |
|---|---|
| Non-Monday week | 422 with stable validation code |
| Missing/foreign owner | 404/403 without data leakage |
| Duplicate `(day, slot)` | 422 before command dispatch |
| Confirmed-plan mutation | Stable 409 or approved supersession behavior |
| Idempotency replay/conflict | Same result for same fingerprint; conflict for reuse |
| Existing routes | `/v1/meal-catalog`, `/v1/meal-recommendations`, `/v1/meal-suggestions` unchanged |

## Function / Interface Checklist

- `CatalogMealRepositoryPort` remains compatible with browse, snapshot, and
  materialization callers.
- `GetWeeklyBudgetQuery` is the only target source for generation; preserve the
  `remaining_days`-includes-today rule.
- `RecommendedMealMaterializationService` remains the nutrition/meal-write
  seam for catalog logging.
- Event-bus composition has one registration path for each new command/query.

## Dependency Map

- Blocks Phases 2-6.
- Depends on current catalog and recommendation contracts, but does not modify
  their public behavior.
- Production content dependency is recorded, not solved, in this phase.

## Success Criteria

- [ ] Contract matrix contains every handoff field, path, status, and error.
- [ ] Lifecycle, idempotency, allergy, and regeneration decisions are explicit.
- [ ] Compatibility tests prove existing catalog/recommendation behavior stays stable.

## Risk Assessment

The largest risk is implementing the mobile mock's names literally while the
backend already has a different catalog authority. Mitigate with contract
tests and a single ID mapping (`recipe_id == catalog_meal_id` at the API edge).

## Security Considerations

All reads and writes require Firebase-authenticated owner scope. Do not expose
user IDs, idempotency fingerprints, raw prompts, provider payloads, or internal
catalog resolver details in responses or logs.

## Next Steps

Carry the approved matrix into the catalog migration and Pydantic schemas.
