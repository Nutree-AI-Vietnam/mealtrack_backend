---
phase: 2
title: "Enrich And Serve Catalog Micronutrients"
status: complete
priority: P1
effort: "3-5d"
dependencies: [1]
---

# Phase 2: Enrich And Serve Catalog Micronutrients

## Overview

Add a shared catalog-level enrichment service and show its persisted
per-serving micronutrient snapshot in recipe detail opened during plan creation.
The client opts in only from the opened detail screen; plan-card detail
preloads remain provider-free. The detail action is an authenticated POST so
the read-only recipe endpoint cannot trigger network or persistence side effects.

## Implementation Steps

1. Batch-load linked ingredient food references and FDC details where an
   existing `fdc_id` is available; fill absent source micros only on the exact
   linked reference through the normalized food-reference nutrient path.
2. On recipe-detail cache warm, compute source-backed recipe micros first and
   request a structured AI estimate only for missing fields. Store values and
   provenance under `(catalog_meal_id, content_hash)`; no user-keyed cache or
   repeated model call on hits.
3. Make `WeeklyRecipeService.detail` read the shared cached result when the
   authenticated detail enrichment action is enabled. Fence claim ownership so
   expired workers cannot save or release a newer claim. Keep provider work out
   of plan preloads and recipe list/browse paths; requests seeing a live claim
   return existing source-backed values; retry failed claims under bounded
   backoff.
4. Map values plus per-field source/estimated metadata to recipe-detail API.
   On provider errors, return source values that exist and omit unavailable
   fields without failing plan generation or changing macros.
5. Keep AI estimates confined to recipe detail; meal logging continues to use
   source-backed reference micros and makes no provider call.
6. Add focused tests for FDC ID/unit mapping, source precedence, exact-reference
   nutrient fill, detail cache reuse, estimate labeling, and unchanged macros.
7. Preserve successful USDA records when other lookups fail. Keep estimate
   markers per nutrient and do not merge AI estimates into meal logging.

## Related Code Files

- Modify: `src/app/services/weekly_recipe_service.py`
- Modify: `src/api/routes/v1/meal_plans.py` and recipe response schemas

## Success Criteria

- [x] Different users resolving the same recipe revision share one enrichment.
- [x] FDC nutrients remain attributed to USDA; AI-fallback fields remain
  explicitly estimated in recipe detail.
- [x] Recipe-detail reads require no AI call after cache fill; meal logging does
  not call providers or use recipe-detail estimates.
