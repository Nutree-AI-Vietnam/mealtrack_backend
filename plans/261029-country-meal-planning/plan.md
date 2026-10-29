---
title: "Country-first meal planning"
description: "Suggest meals people in Vietnam and the United States actually want to eat, then vary and adapt those meals without slowing the planner."
status: pending
priority: P1
branch: "feature/country-meal-planning-plan-c8b8"
tags: [backend, flutter, meal-planner, meal-suggestion, catalog]
blockedBy: []
blocks: []
created: "2026-10-29"
createdBy: "agent"
source: product
---

# Country-first meal planning

## Overview

Meal suggestions miss because they are not tied to a country. A week can mix Vietnamese, Japanese, and Korean recipes. "Suggest a meal" asks for Asian, Western, Latin, or Mediterranean, and when that choice is skipped it asks the model for four world regions at once. Dish names are forced into English.

This plan separates food by country first. Vietnam and the United States are the only markets in scope. Japan and Korea stay in the catalog for later, and they are not part of either default. Diversity, adaptation, and a faster planner all happen inside the chosen country.

The plan is the deliverable. No application code changes in this document.

## What happens today

Three surfaces suggest food, and none of them knows a country.

### Weekly planner

The production catalog contract is 180 recipes: 60 Vietnamese, 60 Japanese, and 60 Korean, with breakfast, lunch, and dinner coverage. There is no American pool. See `REQUIRED_CUISINES` and `PRODUCTION_CUISINE_COUNTS` in `src/domain/services/meal_recommendation/catalog_recipe_seed_validator.py`.

`WeeklyPlanGenerationService` can soft-filter on a free-text cuisine string. The match is a case-insensitive substring, not a country. Diet is only `any`, `vegetarian`, or `no-pork`. Diversity is "do not repeat a recipe on the same day, then prefer the least-used recipe." There is no dish-family or protein cap. Ranking still prefers a close calorie match after a practicality key. The service does not read what the user has logged, skipped, or swapped.

Opening the planner sends `auto_generate=true` (`nutree_ai/lib/features/recipes/data/weekly_meal_plan_api.dart`). `GET /v1/meal-plans/current` then builds the week with `WeeklyMealPlanPreferences(people=1)` and no cuisine (`src/api/routes/v1/meal_plans.py`). If that plan has meals, the app keeps it. The client's default cuisine, `Vietnamese` on `MealPreferences`, never reaches generation on that path (`meal_planner_controller.dart`, `_fetchOrCreatePlan`).

`meal_practicality.py` already pushes ordinary Vietnamese breakfast shapes ahead of salmon, steak, sushi, and "kiểu Nhật". That bias never runs as a country filter, and it does not help a user in the United States.

### Suggest a meal

The suggestion screens offer Asian, Western, Latin, and Mediterranean (`v2_input_screen.dart`, `cuisine_selection_screen.dart`). The discovery prompt adds one line, `Cuisine: {region}`, or nothing. The names prompt, when no region is set, asks for distinct Asian, Mediterranean, Latin, and American dishes (`prompt_template_manager.py`). `DISCOVERY_SYSTEM` requires English-only meal names and forbids Vietnamese words (`system_prompts.py`). Saved meals have `origin_country` and `cuisine_type`, and later suggestions do not read them.

The app waits at least 10 seconds before showing suggestions (`MealSuggestionFlow._minLoadingDuration`), even when discovery returns sooner.

### Three-day recommendations

`ThreeDayPlanOptimizer.build_plan` accepts a cuisine set. The create handler does not pass one, so ranking uses the whole active catalog. Ingredient affinity from the last 90 days can count, and only when the log is linked to a canonical `food_reference_id`. Calorie fit still takes most of the weight (about 0.55 to 0.90). Diversity is a 0.10 rerank of the top 30 by ingredient overlap. Skips are stored and are not a negative signal. The weekly planner does not use this affinity at all.

### Signals that are not a country

| Signal | What it is | Why it is not the food country |
|---|---|---|
| `users.language_code` | App language: en, vi, es, fr, de, ja, zh | Many people in Vietnam use English. English does not mean American food. |
| Device locale | `LanguageConfiguration` keeps the language and drops the region (`vi_VN` becomes `vi`) | The country code never reaches the API. |
| `users.timezone` | IANA zone, synced from the device | Useful as a weak hint for Vietnam. A US zone is not enough to decide the food, because people who want Vietnamese food live in the US. |
| Planner `cuisine` | Optional free text on one week | Unset on auto-generate. Not a saved profile choice. |
| `cuisine_region` | Asian / Western / Latin / Mediterranean | Too wide. "Asian" is not a Vietnamese menu. |

## Locked decisions

- The setting is the country whose everyday food the user wants. It is not the app language and not the timezone.
- Version one supports Vietnam (`VN`) and the United States (`US`) only. Do not add a third country in this work.
- Japan and Korea remain in the catalog. They are excluded from both default weeks and from suggestion prompts.
- Ask when the country is unknown. Do not mass-assign the United States because the UI is English, and do not mass-assign Vietnam because a recipe string once said Vietnamese.
- A strong default, used only until the user chooses: Vietnam when the app language is `vi`, or the saved timezone is `Asia/Ho_Chi_Minh` or `Asia/Saigon`. Otherwise show the two-country choice before the first generated week or suggestion. After a choice, stop inferring.
- A user in the United States who wants Vietnamese food selects Vietnam. Location explains the default prompt. The saved choice wins.
- Weekly plans and "suggest a meal" both use that choice. The three-day recommendation uses the same pool when it builds a plan.
- Diversity means different dish families and proteins inside the country. It does not mean mixing countries.
- Adaptation learns from meals they log, slots they keep, suggestions they skip, and recipes they swap away. It cannot pull a Vietnam menu toward Japanese or Korean food.
- Calories stay backend-derived. Allergen and dislike exclusions stay hard filters. Country filtering must not weaken them.
- A United States week turns on when breakfast, lunch, and dinner each have at least five imported American meals. Until then the API returns `market_catalog_not_ready`. The production manifest still targets 60 American meals so the family caps have room. Do not fill a US week with Vietnamese, Japanese, or Korean recipes.
- Curated catalog meals stay the source for the weekly planner. The model is for "suggest a meal now", constrained to the same country menu. Do not replace the Vietnam week with free-form generation.
- American catalog rows are drafted by the existing suggestion generator and published by the existing catalog importer. Nobody types the 60 recipes, their grams-to-calories math, or their photos. A person only clears ingredient names the importer cannot match and near-duplicate reviews. See Phase 2.
- Perceived wait: show discovery names when they return. Remove the fixed 10-second floor. Generate a full recipe after the user picks a name.
- This plan does not replace the 2 October 2026 planner performance review (`plans/reports/meal-plan-performance-review-2026-10-02.md`). Country filtering has to shrink the candidate set those findings already called out.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Food country](./phase-01-food-market.md) | Pending |
| 2 | [Country menus](./phase-02-country-menus.md) | Pending |
| 3 | [Diversity inside the country](./phase-03-diversity.md) | Pending |
| 4 | [Adaptation](./phase-04-adaptation.md) | Pending |
| 5 | [Planner and suggestion experience](./phase-05-experience.md) | Pending |

Phases 1 and 2 are the country split. Phases 3 and 4 make a week feel varied and personal. Phase 5 is the UI and the latency work required so the new behavior is fast to open and fast to change.

## Success criteria

- A Vietnam week contains only `vietnamese` catalog meals. A United States week contains only `american` catalog meals. A US user with no ready American pool gets an empty planner state, not a mixed week.
- Suggestion names for a Vietnam user are everyday Vietnamese dishes for that meal time. Suggestion names for a US user are everyday American dishes. Skipping the old region chips does not produce a four-region mix.
- In a generated 7-dinner week, at least four dish families appear, and no family appears more than twice. The same recipe appears at most twice.
- After the user swaps a recipe off the week, that recipe is not selected again for that week. The next week lowers that dish family rather than banning it forever.
- The first suggestion list is not held for a fixed 10 seconds. An existing week's planner read does not start catalog generation.
- Tests cover the country filter, the empty US pool, dish-family caps, and the prompt text. They do not depend on a live model.

## Out of scope

- Countries other than Vietnam and the United States, including a Japan or Korea picker.
- Collaborative filtering, vector search, store distance, or a new recommendation model.
- Changing calorie math, weekly budget `remaining_days`, or allergen codes.
- Rewriting the October performance review. Phase 5 only does the country-related cuts and the fixed client wait.
- Translating the whole catalog into every app language. Vietnam shows Vietnamese dish names. The United States shows English dish names. Other UI languages keep the country's dish name.

## Sources

- Weekly generation: `src/domain/services/weekly_meal_planner/weekly_plan_generation_service.py`, `meal_practicality.py`, `src/domain/model/weekly_meal_planner/weekly_meal_plan.py`
- Auto-generate: `src/api/routes/v1/meal_plans.py`; client `nutree_ai/lib/features/recipes/data/weekly_meal_plan_api.dart`, `meal_planner_controller.dart`, `meal_planner_state.dart`
- Catalog contract: `src/domain/services/meal_recommendation/catalog_recipe_seed_validator.py`
- Suggestions: `src/domain/services/prompts/prompt_template_manager.py`, `system_prompts.py`; client `v2_input_screen.dart`, `cuisine_selection_screen.dart`, `meal_suggestion_flow_provider.dart`
- Recommendations: `three_day_plan_optimizer.py`, `recipe_scoring_service.py`, `ingredient_affinity_service.py`, `create_three_day_meal_recommendation_command_handler.py`
- Profile: `users.timezone`, `users.language_code` in `src/infra/database/models/user/user.py`
- Performance review: `plans/reports/meal-plan-performance-review-2026-10-02.md`
- Catalog publish path: `docs/meal-catalog-import-schema.md`, `scripts/import_catalog_recipe_seeds.py`, `scripts/convert_excel_recipes_to_manifest.py`, `scripts/generate_catalog_meal_images.py`, `scripts/enrich_catalog_recipe_micronutrients.py`
