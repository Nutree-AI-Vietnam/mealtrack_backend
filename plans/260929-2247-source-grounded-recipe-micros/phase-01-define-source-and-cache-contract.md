---
phase: 1
title: "Define Source And Cache Contract"
status: complete
priority: P1
effort: "1-2d"
dependencies: []
---

# Phase 1: Define Source And Cache Contract

## Overview

Specify nutrient fields, units, per-serving basis, completeness rules, and
source precedence before wiring enrichment into recipe reads or plan builds.

## Related Code Files

- Read/modify: `src/domain/services/food_mapping_service.py`
- Read: `src/infra/adapters/food_data_service.py`
- Read/modify: `src/infra/repositories/food_reference_repository_async.py`
- Read/modify: `src/domain/model/meal_recommendation/catalog_recipe.py`
- Read/modify: `src/infra/database/models/meal_recommendation/catalog_recipe.py`
- Read: `src/infra/repositories/catalog_recipe_repository_async.py`
- Read: `plans/260921-0000-weekly-meal-planner-backend/phase-02-recipe-catalog-detail-foundation.md`
- Read: `plans/260921-0000-weekly-meal-planner-backend/phase-04-plan-recipe-grocery-and-pantry-apis.md`
- Read: `plans/260921-0000-weekly-meal-planner-backend/phase-05-ai-adjustment-and-diary-logging.md`

## Implementation Steps

1. Extend FDC detail mapping for supported micronutrient IDs, preserving FDC
   amount, unit, `fdc_id`, and source identity; do not rewrite macro columns.
2. Define a narrow structured AI contract for missing micronutrients only,
   including per-serving basis, explicit estimated status, and field-level
   source metadata.
3. Persist enrichment separately from `recipe_payload`; key it by catalog ID
   and current `content_hash`, with an expiring global claim for cold misses.
   Claim in a short transaction, call providers outside it, and compare the
   revision again before save. Invalidate naturally after recipe edits.
   Generate any schema change with `./scripts/development/migrate.sh generate`
   and provide a valid downgrade.
4. Define merge policy: verified FDC value wins; AI may fill absent fields only;
   preserve unavailable fields as null and do not invent zero values.
5. Keep micronutrient enrichment status independent of macro nutrition
   readiness and planner eligibility.

## Success Criteria

- [x] Output contract distinguishes FDC values from estimates at field level.
- [x] Cache identity excludes user and includes the catalog content revision.
- [x] Payload digest, catalog content hash, and authoritative macros are not
  mutated by enrichment.
