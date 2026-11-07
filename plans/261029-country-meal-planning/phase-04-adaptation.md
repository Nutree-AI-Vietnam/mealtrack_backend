# Phase 4 — Adaptation

## Outcome

The next week and the next suggestion move toward food the user actually eats, and away from food they reject, without leaving their country.

## Signals

Use signals the product already records. Do not add a rating dialog in this phase.

| Signal | Where it lives | Effect |
|---|---|---|
| Logged meal with a canonical ingredient | 90-day affinity in `IngredientAffinityService` | Raise meals that share those ingredients. Already built for three-day plans. |
| Logged planner slot | `weekly_meal_plan_slots.is_logged` | Same positive ingredient signal when the recipe's ingredients have `food_reference_id`. |
| Swapped-away recipe | Plan update replaces `recipe_id` | That id is banned for the rest of the week. Its `dish_family` gets a penalty on the next week, not a permanent ban. |
| Skipped suggestion or skipped recommendation slot | Session exclude list; `skipped_at` on recommendation slots | Downrank that family for 28 days. |
| Dislikes and allergies | Plan preferences and profile | Stay hard exclusions. Allergies stay on allergen codes. A dislike that names a family (`phở`, `oatmeal`) excludes that family inside the market. |

Weekly generation today ignores affinity, swaps, and skips. Wire it to the same affinity profile the three-day handler already builds, and to a small rejection profile.

## Rejection profile

Keep it deterministic and bounded.

- Keyed by user, market, and `dish_family` or `recipe_id`.
- A swap writes one recipe ban (this week) and one family penalty (weight 1, decaying to 0 at 28 days).
- Three penalties on the same family in 28 days exclude that family for the next generated week only. The week after, it returns at a penalty if the last event is still inside 28 days. It never excludes the whole market.
- Penalties do not cross markets. A rejected Japanese recipe cannot exist in a Vietnam week after Phase 2; do not let old mixed-week swaps change Vietnam family weights unless the recipe's family is in the Vietnam list.
- Cold start: no affinity and no penalties. Country menu and Phase 3 caps are the whole ranking. Affinity confidence already scales ingredient weight; leave that formula. Add family penalty as a sort key ahead of calorie distance, behind the hard market filter.

## What adaptation must not do

- It must not introduce Japanese or Korean meals because the user once logged an ingredient that also appears there.
- It must not override `vegetarian`, `no-pork`, allergies, or dislikes.
- It must not change calories.
- It must not require a new model call.

## Suggest a meal

Pass the rejection families into the discovery prompt as "do not suggest these families", capped at eight names so the prompt stays small. Keep the existing exclude-name list for the current session. A reload in a new session still respects the 28-day family penalty.

## Tests

- Affinity for rice and eggs raises a Vietnam egg plate over an unused family only after family caps are satisfied. Caps win over taste.
- Swapping recipe A off Monday removes A from Tuesday through Sunday in that week.
- Two weeks later the family penalty is zero and the family can be selected again.
- A US rejection does not change a VN week's order.
- Allergen conflict still drops the meal when affinity for its other ingredients is high.

## Done when

The weekly planner and the three-day plan share one affinity read and one rejection read. A user who keeps swapping away noodle soup sees fewer noodle soups next week and still sees a full Vietnam or US menu.
