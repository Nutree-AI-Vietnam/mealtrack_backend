---
phase: 4
title: "Plan Recipe Grocery And Pantry APIs"
status: pending
priority: P1
effort: "4-6d"
dependencies: [2, 3]
mode: deep
---

# Phase 4: Plan Recipe Grocery And Pantry APIs

## Overview

Expose the weekly plan, recipe browser/detail, grocery projection, and pantry
mutation contracts from the handoff. Keep routes thin and route all writes
through Phase 3 commands/queries.

## Context Links

- Handoff endpoint contract, sections 5.1-5.4
- Existing catalog routes: `src/api/routes/v1/meal_catalog.py`
- Existing route registration: `src/api/main.py`
- API schemas/mappers: `src/api/schemas/`, `src/api/mappers/`
- Existing browse service: `src/app/services/catalog_meal_browse_service.py`

## Requirements

- Implement authenticated `GET /v1/meal-plans/current`, `POST /v1/meal-plans/generate`,
  and `PATCH /v1/meal-plans/{plan_id}` with Monday validation, idempotency,
  owner scope, stable 404/409/422/503 mappings, and OpenAPI models.
- Implement `GET /v1/recipes` and `GET /v1/recipes/{recipe_id}` as catalog-backed
  read surfaces. Preserve existing `/v1/meal-catalog*`; share service/mappers,
  do not fork filtering or nutrition calculation.
- Support `q`, diet, max cooking time, cuisine, dislikes/allergies, bounded
  pagination, and localized display text. Filter semantics must be documented;
  allergy claims follow the Phase 1 decision.
- Implement `GET /v1/meal-plans/{plan_id}/groceries` as a derived projection:
  aggregate selected recipe ingredients by canonical ingredient identity,
  normalize compatible units, multiply by people count, subtract pantry stock,
  and report needed/need-more/bought/owned without mutating plan slots.
- Implement `PATCH /v1/meal-plans/{plan_id}/groceries` through a command with
  per-item validation, idempotency, owner scope, and a transaction-safe upsert.

### Contract Surface

| Endpoint | Success | Core behavior |
|---|---|---|
| `GET /v1/meal-plans/current` | 200 / 404 | Resolve requested or timezone-local Monday week |
| `POST /v1/meal-plans/generate` | 201 / replay 200 | Generate or replay an owner-scoped draft plan |
| `PATCH /v1/meal-plans/{plan_id}` | 200 | Apply validated slot/preferences/status changes |
| `GET /v1/recipes` | 200 | Catalog-backed filtered/paginated summaries |
| `GET /v1/recipes/{recipe_id}` | 200 / 404 | Full immutable catalog recipe detail |
| `GET /v1/meal-plans/{plan_id}/groceries` | 200 | Derived ingredient totals minus pantry stock |
| `PATCH /v1/meal-plans/{plan_id}/groceries` | 200 | Idempotent owner-scoped pantry upsert |

At the compatibility edge, `recipe_id` is the existing `catalog_meal_id`.
The plan response remains grouped by day but always contains all 14 slot
coordinates, including null recipes.

## Architecture

Routes depend on the configured event bus and typed Pydantic schemas only.
`GroceryAggregationService` is pure and receives catalog ingredient projections
plus pantry rows; incompatible units stay separate rather than being merged by
name. Recipe list/detail reuses `CatalogMealBrowseService` and a detail query.
No route opens a raw session or directly edits an ORM row.

## Related Code Files

| Action | Files |
|---|---|
| Create | `src/api/routes/v1/meal_plans.py`, `src/api/routes/v1/recipes.py` |
| Create | `src/api/schemas/request/weekly_meal_plan_requests.py`, `recipe_requests.py` |
| Create | `src/api/schemas/response/weekly_meal_plan_responses.py`, `recipe_responses.py` |
| Create | `src/api/mappers/weekly_meal_plan_mapper.py`, `recipe_mapper.py` |
| Create | `src/domain/services/weekly_meal_planner/grocery_aggregation_service.py` |
| Create | grocery/pantry commands, queries, handlers, and package exports |
| Modify | `src/api/main.py`, `src/api/dependencies/event_bus.py`, catalog repository/service for filters/detail |
| Extend | route, schema, mapper, grocery, and API contract tests |

## Implementation Steps

1. Add request/response DTOs with bounded strings, enum values, pagination,
   slot coordinate validation, portion-independent grocery quantities, and
   explicit nullable recipe fields.
2. Implement compact weekly-plan mapping grouped by day with exactly 14 slots;
   include `total_meals_planned`, timestamps, status, preferences, and logged
   meal references without leaking ORM/session state.
3. Add recipe list/detail aliases over the catalog service, preserving current
   `/v1/meal-catalog` response behavior and translation fallback.
4. Implement unit-aware grocery aggregation and pantry status mapping. Use
   canonical `food_reference_id` and category metadata; never merge arbitrary
   translated names.
5. Register routers and handlers, generate OpenAPI, and add route-level
   authorization/error tests.

## Tests Before

- Characterize existing `/v1/meal-catalog` list/detail response and query count.
- Lock catalog-derived calorie/macro mapping before adding recipe aliases.

## Tests After / Scenario Matrix

| Scenario | Expected evidence |
|---|---|
| Current default week | User timezone determines Monday |
| Recipe alias parity | `/recipes` and catalog detail share identity/nutrition |
| Filters/pagination | Bounds, deterministic ordering, no SQL wildcard injection |
| Empty slot | Excluded from groceries and counted correctly |
| People 1..6 | Quantities scale exactly once |
| Pantry owned/bought | Derived status and to-buy counts are correct |
| Unit mismatch | Separate lines or explicit conversion; never silent corruption |
| Cross-owner plan/ingredient | 404/403, no data leakage |
| Confirmed plan | Grocery reads remain available; forbidden structural edits map to 409 |
| OpenAPI | Paths, schemas, auth, and error responses are present |

## Function / Interface Checklist

- Grocery service has no database/provider imports.
- Recipe detail mapper exposes ordered steps, attribution, equipment, allergens,
  and backend-derived nutrition from one catalog projection.
- Pantry repository upsert cannot create duplicate `(plan_id, ingredient_id)`.
- Route registration does not shadow existing `/v1/meal-catalog/{catalog_id}`.

## Dependency Map

- Depends on Phases 2-3.
- Blocks mobile integration and serves the input contract for Phase 5 AI/logging.
- Existing catalog browse routes are regression dependencies.

## Success Criteria

- [ ] All handoff read/write endpoints are authenticated, typed, owner-scoped,
  and visible in OpenAPI.
- [ ] Grocery output is deterministic, unit-safe, people-scaled, and pantry-aware.
- [ ] Existing catalog/recommendation routes remain behaviorally unchanged.

## Risk Assessment

Grocery aggregation is prone to double-scaling and unsafe name-based merges.
Mitigate with canonical IDs, a pure unit policy, exact fixtures, and a rule that
people count affects only grocery quantities.

## Security Considerations

Bound query/pagination and pantry payload sizes. Avoid returning internal
resolver IDs, source metadata not intended for users, or other users' plans.

## Next Steps

Add validated AI proposals and one-tap portion-aware diary logging.
