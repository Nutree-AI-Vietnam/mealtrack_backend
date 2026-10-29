# Phase 5 — Planner and suggestion experience

## Outcome

The country is visible and changeable. The planner opens on the saved week without generating again. Suggestions appear when the names are ready, and a recipe loads after a tap. Switching country does not discard meals already logged.

## Country control

One control, same two choices, in both places:

- Weekly planner, on the preferences summary that today prints cuisine, people, diet, and time (`MealPreferences.summary`).
- Suggest a meal, replacing the Asian / Western / Latin / Mediterranean grid in `v2_input_screen.dart` and `cuisine_selection_screen.dart`.

Copy is the country name ("Vietnam", "United States"), not a region. The line under a meal is family plus time, for example "Rice plate · about 20 min", in the app language. The dish title stays the country's name: Vietnamese script when the app language is `vi` and the market is `VN`; English otherwise.

Changing the country:

- Updates `users.food_market`.
- Regenerates only unlogged slots in the open week.
- Leaves logged slots and their diary entries as they are.
- Refreshes the grocery list from the new unlogged meals plus the logged ones.
- If the new market's catalog is not ready, unlogged slots clear and the empty state explains that United States meals are not available yet. Logged Vietnam meals stay.

Do not offer Japan or Korea on this control.

## Suggestion flow

- Remove `_minLoadingDuration` (10 seconds) in `meal_suggestion_flow_provider.dart`. The loading state ends when discovery returns or fails.
- The list shows names, calories, and family. Full recipe generation stays on the meal the user opens. That second call already exists; do not block the list on it.
- "More ideas" asks for families not shown yet, inside the same country.
- Empty ingredients are allowed. The country menu is enough. Do not fill the gap with a world-cuisine mix.

## Planner open path

- An existing week is a read. `GET /v1/meal-plans/current` must not call generation when a plan row exists. That is already the branch; keep the client from treating a slow read as a reason to post generate again.
- A missing plan generates only after `food_market` is set (Phase 1). The button state is "Choose country" or "Build this week", not a spinner that hides a mixed auto-generate.
- The week grid renders from slot id, name, meal time, calories, and family. It does not wait on grocery totals. The client already sends `include_grocery_count=false`; keep that, and do not reintroduce the default `true` on this screen.
- The swap sheet lists other meals in the same market and slot, ordered by family (families not yet used this week first), then the Phase 4 penalty, then calorie distance. It does not list the other country's catalog.

## Performance that this work changes

The 2 October 2026 review still applies. These items are in this phase because the country split touches them:

- Generation, slot update, and AI adjust call `list_active_meals()` and hydrate the full catalog. The market query has to restrict cuisine in the database and load compact candidate rows (id, cuisine, family, protein key, calories, meal types, times, allergen codes). Full ingredients load for the chosen slots and the grocery view, not for every candidate.
- AI adjust currently prompts with the first 200 recipes. The candidate list is the same market shortlist as the swap sheet, capped at the eligible set for the slots being changed, not 200 arbitrary recipes.
- One-slot swaps must not load every active recipe. Load the current slot recipe and the market shortlist.
- Name localization: Vietnam recipes that already have a Vietnamese name do not go through the cold translation budget on a `vi` response. US names are already English. The 1.5 second plan-name budget stays as a cap, not as something the country work should spend.
- Do not add a model call to the weekly generator. Country, family, and rejection are local filters.

Leave these review items alone unless a country change collides with them: Redis socket timeouts, pool argument wiring, and post-response micronutrient fan-out. Do not precompute future weeks.

## Layout

- Country choice is a two-card picker, full width on phone, same component in planner and suggestion. No region icons that imply a continent.
- Empty US state is a single message and the Vietnam alternative, not a disabled grid of foreign meals.
- Swap sheet is a short list grouped by family, with the current meal pinned. It is not a catalog browser.
- Loading: planner skeleton is the week grid, not a full-screen hold. Suggestion skeleton is the name list. Neither lasts a fixed minimum time.

Check both a phone width and a wider width for the picker and the week grid. The planner is an existing screen; this phase changes the summary, the empty state, and the swap sheet, and those are the flows to exercise.

## Tests

- Widget or unit test: suggestion request after the chip change sends `VN` or `US` and does not send `Asian`.
- Provider test: discovery response is applied without waiting out a 10-second timer.
- API test: current plan for an existing week performs no generate command.
- API test: cuisine-restricted candidate query for `VN` does not return `japanese` or `korean` ids.
- Swap test: logged slot id is unchanged after a market change; an unlogged slot either moves to the new market or becomes the empty reason.

## Done when

A user can see which country the plan is for, switch it without losing logged meals, get a week from that country only, and see suggestion names without a fixed 10-second wait. Opening a saved week does not rebuild it.
