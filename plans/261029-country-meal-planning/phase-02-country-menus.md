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

Build that pool with the tools already in the repo. There is no job today that invents a country menu and inserts it into `meal_catalog`. Saved suggestions stay on the user's meal row. The publish path is the catalog importer.

1. **Draft.** One script calls the existing suggestion generator once per family and meal time, with the US prompt block from this phase. It writes a partial manifest: `recipe_key`, `cuisine: american`, `name`, `meal_types`, `dish_family`, and `ingredients[]` of `name`, `quantity`, `unit`. It leaves calories, macros, and `food_reference_id` empty. The importer computes those. An existing spreadsheet can skip the model and use `scripts/convert_excel_recipes_to_manifest.py` instead.
2. **Resolve.** `scripts/import_catalog_recipe_seeds.py --partial --dry-run` matches ingredient names to verified `food_reference` rows. Exact and high-confidence fuzzy matches resolve themselves. `POST /v1/admin/meal-catalog/enrich` caches a FatSecret candidate for a name that is missing. `--resolve-all-best-effort` is only for a test catalog.
3. **Review.** A person approves the resolver report and any near-duplicate held at ingredient Jaccard `0.80`. That is the remaining manual step. Unresolved names and unreviewed duplicates do not publish.
4. **Import.** The same script, or `POST /v1/admin/meal-catalog/import`, writes the rows. `--partial` is how the first American meals land before the count hits 60. Production validation still requires the full count and the per-slot minimum.
5. **Photos and micros.** `scripts/generate_catalog_meal_images.py` fills `image_url`. `scripts/enrich_catalog_recipe_micronutrients.py` fills micronutrients. `scripts/estimate_catalog_macros_ai.py` is only for rows that still have no food-reference mapping. Mapped rows keep backend-derived calories.

Until `american` meets the production count and the per-slot minimum, `food_market=US` generates zero slots and the API returns a typed empty reason (`market_catalog_not_ready`) rather than falling through to other cuisines. A partial import can already serve a US week once each slot type has five eligible meals; the 60-recipe gate stays on the production manifest, not on the first usable week.

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

- A drafted American manifest with null `food_reference_id` dry-runs through `import_catalog_recipe_seeds.py --partial` and reports ingredient issues instead of inventing calories.
- Seed validator accepts a full American manifest and rejects production when American coverage is short, once American is required. Until the manifest exists, the runtime empty reason is what US users hit; do not weaken the Vietnam/Japan/Korea counts to ship the column.
- Generator fixture: 3 Vietnamese + 3 Japanese meals, market `VN`, 21 slots. Every selected id is Vietnamese. No fallback.
- Prompt snapshot tests: Vietnam discovery contains the family list and does not contain "Asian, Mediterranean, Latin, American". Vietnamese-language discovery does not contain the English-only name ban. US discovery does not mention phở or kimchi as required variety.

## Done when

A Vietnam user cannot be served Japanese or Korean food by the planner, the three-day plan, or suggestion prompts. A US user sees American meals once the catalog is ready, and an explicit not-ready state before that. They never see the Vietnam/Japan/Korea week as a substitute.
