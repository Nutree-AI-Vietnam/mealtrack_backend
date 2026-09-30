---
phase: 1
title: "Backend Ingredient Identity Guards"
status: completed
effort: ""
---

# Phase 1: Backend Ingredient Identity Guards

## Overview

Prevent a nullable `food_reference_id` from crashing snapshot-scoped ingredient statistics and ranking. Normalize repository ID inputs defensively while preserving positive canonical IDs and existing public-reference eligibility.

## Implementation Steps

1. Update `src/domain/services/meal_recommendation/catalog_ingredient_statistics_service.py` so missing/non-positive ingredient IDs are excluded before comparison and IDF counting; preserve catalog size and deterministic ordering.
2. Update `src/infra/repositories/food_reference_repository_async.py:get_nutrition_projections` (the failing log path) and its sibling `get_by_ids` to accept mixed nullable/malformed input safely, retain only valid positive IDs, deduplicate/sort, and return before SQL when none remain.
3. Add focused regression coverage in `tests/unit/domain/services/meal_recommendation/test_catalog_ingredient_statistics_service.py` and `tests/unit/infra/repositories/test_food_reference_repository_async.py`; extend `tests/unit/app/services/test_catalog_meal_snapshot_service.py` or `tests/unit/app/services/test_catalog_meal_browse_service.py` only if needed to prove ranking consumes the safe snapshot.
4. Run the focused unit tests, then the backend unit suite and static checks required by the backend release gate. Do not add a migration or alter endpoint shapes.

## Success Criteria

- [x] A catalog meal with `food_reference_id=None` does not raise while statistics are built or ranked.
- [x] Missing, non-numeric, zero, and negative IDs do not enter IDF or the repository query; valid string/integer IDs retain canonical positive values.
- [x] Empty normalized repository input issues no query; valid IDs retain current ordering, public-eligibility filtering, and returned rows.
- [x] Focused and broader backend checks pass with no API/schema change.

## Validation Evidence

- Focused nullable-ID and micronutrient regressions: 50 passed.
- Backend unit suite: 3,329 passed; Ruff checks and formatting passed for the changed files.

## Related Code Files

- Modify: `src/domain/services/meal_recommendation/catalog_ingredient_statistics_service.py`
- Modify: `src/infra/repositories/food_reference_repository_async.py` (`get_nutrition_projections` and `get_by_ids`)
- Tests: `tests/unit/domain/services/meal_recommendation/test_catalog_ingredient_statistics_service.py`
- Tests: `tests/unit/infra/repositories/test_food_reference_repository_async.py`
- Regression seam: `src/app/services/catalog_meal_snapshot_service.py`, `src/app/services/catalog_meal_browse_service.py`

## Risk Assessment

Invalid-ID filtering must not silently map an unknown value to a real food ID. Only accepted positive canonical IDs may reach SQL or ranking; unmapped ingredients remain absent from similarity statistics.
