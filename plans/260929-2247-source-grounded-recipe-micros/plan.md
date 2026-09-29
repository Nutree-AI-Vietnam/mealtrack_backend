---
title: "Source-Grounded Recipe Micronutrient Enrichment"
description: "Persist source-grounded recipe micronutrients for weekly plan recipe detail."
status: complete
priority: P1
branch: "delivery"
tags: []
blockedBy: []
blocks: []
created: "2026-09-29T15:47:27.676Z"
createdBy: "ck:plan"
source: skill
---

# Source-Grounded Recipe Micronutrient Enrichment

## Overview

Fill missing recipe micronutrients from linked USDA FoodData Central records,
then use a documented AI estimate only for remaining fields. Cache by shared
catalog recipe revision so users share one enrichment result. Run this from
recipe detail opened during plan creation; the logging path stays independent.

## Current Evidence

- `GET /v1/recipes/{recipe_id}` maps `CatalogMeal.nutrition_micros`; catalog
  detail currently aggregates linked `food_reference.extra_nutrients`.
- FDC mapping in `food_mapping_service.py` currently extracts macros only.
- Catalog meal logging recomputes micros from canonical food references and
  persists `Meal.Nutrition.micros`; estimates remain recipe-detail only.
- `meal_catalog.recipe_payload`, `payload_digest`, and `content_hash` describe
  catalog content. Keep generated nutrition in separate persisted state.

## Decisions

- Cache at catalog scope by `(catalog_meal_id, content_hash)`, shared across
  users; use a revision-scoped claim token so stale requests cannot save or
  release a newer claim. Make the claim in a short transaction; call providers
  outside it.
- Prefer linked FDC nutrients with units and per-field origin metadata. Keep
  successful FDC records when another linked lookup fails. Estimate only
  unresolved micronutrient fields; provider failure leaves those fields
  unavailable.
- Never use FDC or AI enrichment to modify calories, protein, carbs, fat, or
  fiber. Preserve catalog payload digest and content hash.
- Warm only through an authenticated POST when the user opens recipe detail
  during plan creation; normal plan and recipe preloads remain provider-free.
  Serve persisted values on later detail reads. Meal logging never invokes
  providers or consumes AI estimates.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Define Source And Cache Contract](./phase-01-define-source-and-cache-contract.md) | Complete |
| 2 | [Enrich And Serve Catalog Micronutrients](./phase-02-enrich-and-serve-catalog-micronutrients.md) | Complete |

## Dependencies

Add this scope to the existing weekly planner recipe/detail/log integration;
reuse its catalog and logging paths rather than creating a second recipe source.
Related plan: `../260921-0000-weekly-meal-planner-backend/`.

## Success Criteria

- [x] Shared revision cache prevents repeated or concurrent per-user AI calls.
- [x] FDC values retain units and source identity; AI values remain explicitly
  estimated and only fill missing micronutrient fields.
- [x] Recipe detail exposes persisted, per-serving micros with provenance;
  macro values and catalog digests remain unchanged.
- [x] FDC/provider failure preserves existing recipe and plan behavior.
