# Phase 2 — Country menus

## Outcome

Vietnam and the United States each have a menu of everyday food for breakfast, lunch, and dinner. Suggestions and the weekly planner draw only from the user's menu.

## Vietnam catalog

The seed contract already requires 60 active `vietnamese` recipes and at least five per breakfast, lunch, and dinner. Keep that pool. Review it against "would someone cook this at home this week", using the shapes already encoded in `meal_practicality.py`:

- Breakfast stays with familiar forms (eggs, bánh mì, cháo, xôi, yogurt, oatmeal). Salmon porridge, steak, sushi, and "kiểu Nhật / Pháp / Ý" titles do not lead the week.
- Drop or unpublish titles that are drinks, desserts, remedies, or articles (`_NON_MEAL_TITLE_WORDS` is the current list). They must not occupy a slot.
- Do not add Japanese or Korean recipes to raise variety.

Add a `dish_family` on each Vietnam recipe. Keep the set small and stable:

| Slot | Families |
|---|---|
| Breakfast | `egg_plate`, `porridge`, `sticky_rice`, `bread`, `noodle_soup` |
| Lunch and dinner | `rice_plate`, `noodle_soup`, `noodle_stir`, `grilled`, `braised`, `soup`, `fresh_salad` |

A recipe can be eligible for more than one slot and still has one family. Store the family on the catalog row (seed field plus column via the migration CLI). Do not infer the family from the title on the request path; titles are already expensive to regex (`weekly_plan_generation_service.py` scans titles per slot).

## United States catalog

Add `american` as a fourth cuisine in the seed validator, with the same bar: 60 recipes, at least five breakfast, five lunch, and five dinner, planner-eligible and nutrition-ready. This is everyday home food, not a restaurant tour and not "Western" (no requirement to include French, British, or Italian as the identity of the meal).

Families:

| Slot | Families |
|---|---|
| Breakfast | `eggs`, `oatmeal`, `toast`, `yogurt`, `breakfast_sandwich` |
| Lunch and dinner | `sandwich`, `salad`, `grain_bowl`, `pasta`, `grilled_plate`, `soup_stew`, `taco` |

The last catalog was slow because a person had to find the dishes and then shape them for the importer. `scripts/convert_excel_recipes_to_manifest.py` only converts a spreadsheet column named `Structured JSON`. `docs/meal-catalog-import-schema.md` still expects that manifest. Saved suggestions never enter `meal_catalog`. That research-and-format loop is not the path for a new country.

One command fills a country:

```bash
python scripts/fill_country_catalog.py --market US
```

The same command with `--market VN` can top up Vietnam later. The person running it does not pick dishes, open a spreadsheet, or write JSON.

What the command does, in order, with code that already exists:

1. Ask the existing suggestion generator for one everyday dish per family and meal time, using the country prompt in this phase. The model is the thing that finds the dishes. If a reply is incomplete, it asks again for that family until the slot minimum is met or the retry cap is hit.
2. Map that reply onto the importer's manifest in memory: `recipe_key`, `cuisine`, `name`, `meal_types`, `dish_family`, and ingredients as `name`, `quantity`, `unit`. The script owns that shape. It is not a file someone edits.
3. Resolve each ingredient through `NutritionLookupService` and `IngredientNutritionResolver`. Those already match `food_reference` and, on a miss, search FatSecret and store the food. Calories stay backend-derived from that match.
4. Call `CatalogMealSeedImporter` in process with partial import and best-effort resolution. Near-duplicates are skipped and another dish is requested for that family. The command does not stop for a resolver-map file or a review disposition.
5. Generate photos with the existing catalog image generator for the rows it inserted, then enqueue the existing micronutrient enrichment.

The command prints one report: inserted, skipped as duplicate, skipped as unresolved, and whether breakfast, lunch, and dinner each reached five meals. A US week stays on `market_catalog_not_ready` until that minimum is in the catalog. The production manifest target of 60 is how many the command aims to insert, not a file someone prepares beforehand. Re-running the command continues from what is already imported. It does not replace rows.

Japan and Korea stay at 60. They are not selectable in this phase.

## Suggestion prompts

Discovery and meal-name prompts gain a country block and lose the region mix.

For `VN`, the prompt says: everyday food someone in Vietnam would eat for this meal time; choose different families from the Vietnam list for that slot; use the Vietnamese dish name when the user's language is `vi`, and the English name otherwise. Do not invent Japanese, Korean, or American dishes to look diverse.

For `US`, the same shape with the American family list and English names.

Remove these current instructions when a market is set:

- `Cuisine: {Asian|Western|Latin|Mediterranean}` as the only constraint (`build_discovery_prompt`).
- `Cuisines: N distinct (Asian, Mediterranean, Latin, American)` when the region is omitted (`build_meal_names_prompt`).
- `ALL meal names MUST be in ENGLISH ONLY` and the ban on Vietnamese words in `DISCOVERY_SYSTEM`, for `VN` users whose language is `vi`. English names remain required for `US`, and for internal ingredient lines the nutrition estimator already expects English. Dish names and ingredient lines are different fields; do not translate "phở bò" into "beef noodle soup" on a Vietnamese UI.

`origin_country` on the recipe JSON is `Vietnam` or `United States`, matching the market. It is not a free model guess.

The discovery fallback builder must use the same family list. A provider failure cannot return a generic international meal.

## Planner and recommendation selection

`WeeklyPlanGenerationService` hard-filters to the market cuisine before practicality and calories. A soft cuisine miss must not fall back to the other cuisines. Today `_soft_eligible` failure falls back to all hard candidates (`candidates or hard_candidates`), which is how a thin cuisine filter becomes a mixed week. Delete that fallback for market.

`CreateThreeDayMealRecommendationCommandHandler` passes the user's market into `build_plan(..., cuisines=...)`. If the pool is smaller than the plan, return the existing insufficiency result. Do not widen the cuisine set.

## Tests

- `fill_country_catalog.py --market US` builds the importer payload itself, imports without a hand-written manifest, and stops at five meals per slot or the retry cap. A fixture generator response is enough. No spreadsheet and no resolver-map file.
- Seed validator accepts a full American manifest and rejects production when American coverage is short, once American is required. Until the manifest exists, the runtime empty reason is what US users hit; do not weaken the Vietnam/Japan/Korea counts to ship the column.
- Generator fixture: 3 Vietnamese + 3 Japanese meals, market `VN`, 21 slots. Every selected id is Vietnamese. No fallback.
- Prompt snapshot tests: Vietnam discovery contains the family list and does not contain "Asian, Mediterranean, Latin, American". Vietnamese-language discovery does not contain the English-only name ban. US discovery does not mention phở or kimchi as required variety.

## Done when

A Vietnam user cannot be served Japanese or Korean food by the planner, the three-day plan, or suggestion prompts. A US user sees American meals once the catalog is ready, and an explicit not-ready state before that. They never see the Vietnam/Japan/Korea week as a substitute.
