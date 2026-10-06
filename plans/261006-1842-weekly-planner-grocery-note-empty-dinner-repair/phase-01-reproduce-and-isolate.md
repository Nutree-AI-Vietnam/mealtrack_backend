---
phase: 1
title: "Reproduce and isolate"
status: complete
priority: P1
effort: "0.5d"
dependencies: []
---

# Phase 1: Reproduce and isolate

## Overview

The screenshots and read-only production data isolate the legacy two-slot plan shape. Keep device UI state separate from fresh server state during release verification.

## Evidence

- Three production draft `v1` plans for week 2026-10-05 contain seven slot-0 and seven slot-1 rows and no slot-2 row. The newest was created at 14:31 local time, matching the third screenshot. All were written after the October 3 repair migration.
- The latest plan's slot-0 recipes are lunch-eligible and slot-1 recipes dinner-eligible. The current app labels indexes 0, 1, 2 as breakfast, lunch, dinner, so it shifts the two saved meals in the UI and shows dinner empty.
- Production has the grocery day-note table and 751 planner-eligible dinner recipes. The shown Samyang recipe is eligible. The missing dinner is not a catalog shortage.
- Current backend domain validation requires 21 slots and rejects the 14-slot aggregate. The exact production HTTP response and active Render image are still pending verification.

## Context Links

- Backend route: `src/api/routes/v1/meal_plans.py:246-290,571-597,675-753`.
- Flutter client: `lib/features/recipes/data/weekly_meal_plan_api.dart:51-85,241-305`.
- Existing release checks: `../260930-1204-weekly-planner-release-blockers/phase-02-mobile-weekly-planner-validation.md`.

## Implementation Steps

1. Capture initial `GET /v1/meal-plans/current` and `GET /v1/meal-plans/{id}/groceries`, then compare Tuesday dinner `slot_id`, embedded recipe, `total_meals_planned`, catalog activity and saved preferences with Flutter `state.plan[1][dinnerSlot]`.
2. Save a day amount and toggle its covered state. Observe `PATCH .../grocery-days` status/body, request ingredient ID and lines, follow-up grocery read, screen reopen, and Undo. Check whether the sheet closes before an asynchronous rejection (`day_ingredient_amount_sheet.dart:101-110`).
3. Select a valid dinner recipe for the empty slot. Observe recipe-detail and grocery-preview requests, then whether the PATCH runs. If it does, capture `RECIPE_NOT_FOUND`, `RECIPE_SLOT_INELIGIBLE`, `RECIPE_INELIGIBLE`, `WEEKLY_PLAN_STALE_REVISION`, or another actual code.
4. Classify each defect as persisted slot, response projection, client parse/render, preflight, write, or deployment mismatch. Keep hypotheses unresolved until request and fresh-read evidence agree.

## Success Criteria

- [x] Stored slot shape and meal types explain the missing dinner and failed writes.
- [x] Production read-only diagnosis made no data changes.
- [ ] Capture active Render revision and fresh HTTP responses during release verification.
