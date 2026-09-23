---
title: "Weekly Meal Planner Backend"
description: "Add an owner-scoped 7-day meal planner, recipe detail surface, grocery aggregation, pantry state, AI diffs, and diary logging on top of the existing catalog foundation."
status: pending
priority: P1
branch: ""
effort: "4-6 weeks"
tags: [feature, backend, database, api, critical]
blockedBy: []
blocks: []
created: "2026-09-21T04:55:25.370Z"
createdBy: "ck:plan"
source: skill
mode: deep
---

# Weekly Meal Planner Backend

## Overview

Implement the supplied Weekly Meal Planner & Recipes handoff as an additive
bounded context. Reuse the existing immutable `meal_catalog`, canonical
`food_reference` nutrition, catalog snapshot/ranking services, CQRS event bus,
and catalog-to-diary materialization. Do not revive the dropped generic
`meal_plans` table or alter the existing three-day recommendation contract.

The public API keeps the handoff paths (`/v1/meal-plans/*` and
`/v1/recipes*`). Storage uses explicit `weekly_meal_*` names. Recipe detail
enrichment extends the current catalog rather than introducing a duplicate
`recipes` authority. Generation is deterministic; Ask Nutree returns a
validated proposal and never mutates a plan without an explicit PATCH.

## Scope

- In scope: Monday-based ISO weeks, 14 lunch/dinner slots, draft/confirmed
  lifecycle, people/preferences, deterministic generation, slot edits, recipe
  list/detail, groceries, pantry state, structured AI diffs, portion-aware
  diary logging, localization, OpenAPI, tests, rollout gates, and docs.
- Out of scope: replacing `/v1/meal-suggestions`, changing three-day
  recommendation semantics, checkout/shopping fulfillment, client changes,
  unbounded free-text recipe creation, or claiming allergy safety without an
  approved allergen data policy.

## Architecture Decisions

1. `weekly_meal_plans`, `weekly_meal_plan_slots`, and
   `weekly_meal_plan_pantry_items` are new user-owned tables. The current
   `meal_catalog`/`meal_catalog_ingredients` remain the recipe and nutrition
   authority; add a dedicated immutable catalog-steps relation because the
   existing normalized meal-step rows belong to user-created `meal` records.
2. Catalog calories/macros are derived from canonical food references through
   the existing conversion/integrity path. Client values and people-count
   scaling never become nutrition authority.
3. Plan writes are owner-scoped, idempotent, transactionally locked, and
   generated through CQRS. Reuse the existing durable-write records for
   request replay rather than adding a second generic operation ledger.
   Groceries are a read-time projection from selected catalog ingredients minus
   plan pantry quantities.
4. All migrations use `./scripts/development/migrate.sh generate`; no revision
   or migration filename is hand-created.

## Related Existing Work

- `plans/260716-1509-four-table-meal-catalog-rework/` — current catalog and
  recommendation foundation; blocked for production corpus readiness, not a
  reason to duplicate its tables.
- `plans/260720-0211-meal-recommendation-performance-redesign/` — reuse its
  snapshot and compact-read principles; do not change its existing contract.
- `plans/260729-1930-preparation-aware-catalog-resolution/` — production
  catalog data dependency; code planning can proceed with fixtures, release
  cannot claim content readiness until this is resolved.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Contract Reconciliation And Product Gates](./phase-01-contract-reconciliation-and-product-gates.md) | Pending |
| 2 | [Recipe Catalog Detail Foundation](./phase-02-recipe-catalog-detail-foundation.md) | Pending |
| 3 | [Weekly Plan Persistence And Generation](./phase-03-weekly-plan-persistence-and-generation.md) | Pending |
| 4 | [Plan Recipe Grocery And Pantry APIs](./phase-04-plan-recipe-grocery-and-pantry-apis.md) | Pending |
| 5 | [AI Adjustment And Diary Logging](./phase-05-ai-adjustment-and-diary-logging.md) | Pending |
| 6 | [Verification Documentation And Rollout](./phase-06-verification-documentation-and-rollout.md) | Pending |

## Dependencies

- Phase order is sequential: 1 → 2 → 3 → 4 → 5 → 6.
- Existing catalog/recommendation plans are compatibility constraints, not
  implementation blockers for the API skeleton.
- Production rollout depends on a reviewed catalog corpus and an explicit
  allergy-evaluation decision.

## Success Criteria

- [ ] Authenticated users can create/read/update one Monday-based weekly plan
  with exactly 14 durable lunch/dinner slots and owner isolation.
- [ ] Generation is deterministic, budget-aware, preference-aware, idempotent,
  and does not change existing recommendation or meal-suggestion routes.
- [ ] Recipe detail, groceries, pantry updates, AI proposals, and portion-aware
  diary logging match the handoff contract and OpenAPI.
- [ ] Backend-derived calories survive logging; no client nutrition is trusted.
- [ ] Unit, migration, architecture, and PostgreSQL concurrency gates pass;
  rollout remains disabled until catalog/content gates are green.

## Open Questions / Product Gates

1. Recommended: reject generation/slot edits against a `confirmed` plan with a
   conflict and require a new draft; alternatively, define an explicit
   confirmed-plan regeneration/supersession contract before Phase 3.
2. Existing product memory says allergy handling is disclosure-only with
   `allergy_evaluated=false`; the handoff requests filtering and matching.
   Choose whether this release may implement authoritative allergen filtering
   or must retain disclosure-only behavior until canonical allergen data exists.

## Validation Log

### Planner self-verification

- Existing route/model/repository paths were checked for catalog browse,
  deterministic recommendation, weekly-budget lookup, CQRS registration, and
  catalog meal materialization.
- The handoff's `recipes`/`meal_plans` schema was reconciled with the live
  four-table catalog and the dropped legacy plan tables.
- No external dependency research is required; implementation uses existing
  FastAPI, SQLAlchemy, Alembic CLI, PyMediator, OpenAI structured-generation,
  and translation patterns.

### Whole-plan consistency sweep

- Files reread: `plan.md` and all six `phase-*.md` files.
- Decision deltas checked: storage authority, route aliases, 14-slot shape,
  nutrition derivation, AI proposal-only behavior, diary linkage, and rollout
  evidence boundaries.
- Reconciled stale terms: generic `meal_plans`/`recipes` storage, client macro
  authority, direct AI mutation, and unsupported allergy-safety claims.
- Unresolved product gates: confirmed-plan regeneration and allergy-evaluation
  policy remain explicitly listed above; no implementation contradiction remains.

### Planner adversarial review

- Dedicated catalog steps prevent user-created meal rows from becoming recipe
  authority.
- Existing durable-write records cover weekly mutation replay; no second generic
  idempotency ledger is planned.
- Weekly diary logging explicitly claims weekly slots and does not reuse the
  three-day recommendation slot lookup.
- People count is restricted to grocery scaling; calories remain macro-derived.
- Existing catalog/recommendation routes are regression gates, not migration
  targets. Remaining risks are the two named product gates and real catalog
  readiness, not hidden implementation assumptions.
