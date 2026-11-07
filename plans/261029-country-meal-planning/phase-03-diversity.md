# Phase 3 — Diversity inside the country

## Outcome

A week in one country does not repeat the same kind of meal. Variety comes from dish family and main protein, not from another country's recipes.

## Rules

Apply these in `WeeklyPlanGenerationService` after the market filter, and use the same family rule when discovery asks for several names.

For one week, one user, one market:

- At most two slots in the same meal time share a `dish_family`. Seven dinners need at least four families. If the pool cannot satisfy that, fill the remaining slots and report `diversity_relaxed` on the plan. Do not relax the market filter to meet the cap.
- The same `recipe_id` appears at most twice. Same-day repeats stay forbidden.
- At most three dinners share a main protein. Protein is the highest-gram canonical ingredient in the protein category, else the first meat or tofu token already used by the diet filter. Missing protein does not fail the slot.
- Breakfast keeps the practicality rank from `meal_practicality.py`. A closer calorie match does not outrank a normal breakfast shape inside the same family penalty.
- Calorie distance breaks ties inside the same family-use and protein-use bucket. It is not the first key. A meal 15% off the slot target that keeps the family cap beats a perfect calorie match that would be the third noodle soup.

Current sort key, for contrast, is: already used today, practicality, times used this week, calorie distance, stable hash. Family and protein are absent, so the calorie-closest rice plate can win every dinner.

Suggestion discovery asks for `count` names and says which families are still unused in that response. The exclude list stays, and it is no longer the only diversity tool.

Three-day ranking keeps ingredient-overlap reranking. Add the family cap on top of the market filter before calorie fit can pick three similar plates. Do not raise the diversity weight by pulling in other cuisines.

## Stable output

Keep the user-and-week hash as the last tie-break. The same inputs still produce the same week. Family caps are deterministic, so this does not add randomness.

## Tests

- Seven dinners, two families in the fixture: the generator uses both, hits the cap, and sets `diversity_relaxed` only after both families are used twice.
- A fixture with four families never emits a third noodle soup when other families still have eligible meals.
- Calorie-perfect Japanese meals in a Vietnam market stay unselected. This phase must not undo Phase 2.
- Discovery prompt for six meals names six different families when the slot has at least six.

## Done when

A Vietnam week is still entirely Vietnamese, and its dinners are not seven variants of the same plate. A US week follows the same caps on American families.
