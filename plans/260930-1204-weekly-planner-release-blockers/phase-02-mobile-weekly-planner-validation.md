---
phase: 2
title: "Mobile Weekly Planner Validation"
status: in_progress
effort: ""
---

# Phase 2: Mobile Weekly Planner Validation

## Overview

Complete the outstanding mobile checks for plan cache/reopen behavior, local grocery projection and latency, first-render macros, rounded checkbox state, and recipe portion logging/navigation. Keep UI optimism separate from server persistence proof.

## Implementation Steps

1. Inspect/fix the relevant controller, state, screen, grocery widgets, meal-card nutrition widgets, and recipe-detail log action in the Flutter repository.
2. Add or update focused coverage in `test/features/recipes/application/meal_planner_controller_test.dart`, `test/features/recipes/presentation/screens/meal_planner_screen_test.dart`, `test/features/recipes/presentation/screens/recipe_detail_screen_test.dart`, `test/features/recipes/presentation/widgets/grocery_by_date_view_test.dart`, `test/features/recipes/presentation/widgets/grocery_item_tile_test.dart`, and `test/features/recipes/weekly_meal_plan_api_test.dart` as appropriate.
3. Verify locally: plan and grocery state after navigation away/back and fresh load; grocery quantities/check state before and after server reconciliation; macros visible on the first rendered meal card; rounded checkbox shape and checked/unchecked states; log-portion bottom bar state and return to the correct planner route/slot.
4. Run focused Flutter tests and the app's normal analyzer/build checks after fixes.
5. On authenticated staging/device, repeat the applicable read/write flows. Reopen the plan and fetch fresh state after mutations; record grocery projection and request latency with environment, sample count, warm/cold state, and p50/p95. Compare only with an existing release SLO.

## Success Criteria

- [ ] Cache/reopen and grocery edits survive a fresh backend read and route reopen; local-only updates are not mistaken for persistence.
- [ ] Grocery projection remains responsive and matches server-calculated totals/check state after reconciliation; timing evidence is recorded separately from widget-test timing.
- [ ] Macros render on first frame from backend-provided nutrition without client calorie derivation.
- [ ] Rounded grocery checkbox toggles accurately and retains state after refresh/reopen.
- [ ] Logging a portion updates the correct slot, displays the expected bottom-bar state, and returns to the planner without losing context.
- [ ] Focused Flutter tests pass; staging/device evidence is recorded separately and no browser-only simulator claim is used as device proof.

## Related Code Files

- Likely modify: `lib/features/recipes/application/meal_planner_controller.dart`, `lib/features/recipes/domain/models/meal_planner_state.dart`, `lib/features/recipes/presentation/screens/meal_planner_screen.dart`
- Likely modify: `lib/features/recipes/presentation/screens/recipe_detail_screen.dart`
- Likely modify: `lib/features/recipes/presentation/widgets/groceries/grocery_by_date_view.dart`, `lib/features/recipes/presentation/widgets/groceries/grocery_item_tile.dart`
- Inspect as needed: `lib/features/recipes/presentation/widgets/meals/meal_card.dart`, `lib/features/recipes/presentation/widgets/meals/meal_card_nutrition_pills.dart`
- Tests: the matching `test/features/recipes/...` files listed in step 2.

## Validation Evidence

- The controller plan-switch rollback regression, cached reopen test, grocery/portion tests, recipe-detail route tests, and related focused Flutter suite pass.
- Full Flutter suite passed: 3,785 tests; full `flutter analyze` passed with no issues.
- Staging fresh-read persistence and live p50/p95 latency remain unverified; retain this phase as in progress until the deployed revision is measured.

## Integration Boundaries

- Mobile uses backend-returned macros/calories and backend persistence for server plans.
- Local grocery projection may provide immediate display, but backend reconciliation and fresh reads decide persisted state.
- Do not claim staging/device success from mocks, widget tests, local builds, screenshots alone, or a UI-only state update.
