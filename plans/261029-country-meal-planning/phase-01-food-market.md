# Phase 1 — Food country

## Outcome

Each user has one saved food country, `VN` or `US`, or no choice yet. The weekly planner, meal suggestion, and three-day recommendation read that value. They do not infer it again after it is saved.

## Data

Add nullable `users.food_market` as `VN` or `US`. Generate the migration with `./scripts/development/migrate.sh generate "add user food market"` and implement `downgrade()`. Leave existing rows null. Do not backfill from `language_code`.

Store the market used for a week on that week's plan preferences, next to the current cuisine string. A later profile change does not rewrite logged slots. The next unlogged generation uses the new market.

Map the market to the catalog cuisine exactly:

| Food market | Catalog `cuisine` |
|---|---|
| `VN` | `vietnamese` |
| `US` | `american` |

Stop treating planner cuisine as a free substring. `Vietnamese` matching `vietnamese` by accident is not the contract. Unknown or empty market means "not chosen", not "all cuisines".

## How the first value is chosen

Only before `food_market` is null:

1. App language `vi` → `VN`.
2. Else saved timezone `Asia/Ho_Chi_Minh` or `Asia/Saigon` → `VN`.
3. Else do not guess. The first planner open and the first suggestion open show Vietnam and the United States and wait for a tap.

`en`, a US timezone, and a device region of `US` do not auto-select `US`. People who want Vietnamese food live in the United States, and English is a common app language in Vietnam. The client may display "You are in the US" as helper copy when the timezone starts with `America/`. The button they tap is still an explicit choice.

The app language picker drops the region (`vi_VN` → `vi` in `LanguageConfiguration`). Do not start sending locale region as a silent override. Language `vi` is the only locale signal this phase trusts, and only as the default above.

## API

- Read and update `food_market` on the existing profile or user settings surface. Reject any value other than `VN`, `US`, or null (clear is not required; changing between the two is).
- `GET /v1/meal-plans/current` with `auto_generate=true`: if `food_market` is null, return the current not-found behavior for a missing plan instead of inserting a mixed week. The client then shows the country choice. If `food_market` is set, generate with that market.
- `POST /v1/meal-plans/generate` uses the saved market when the body omits cuisine. A body market that disagrees with the profile updates the profile, so the planner control and the profile cannot drift.
- Discover and recipe generation take the saved market when `cuisine_region` is absent. Ignore Asian / Western / Latin / Mediterranean as a country. Phase 5 removes those chips.

## Client

- Persist nothing extra beyond the server field. The local `MealPreferences.cuisine = 'Vietnamese'` default is removed so a cold start cannot disagree with the server.
- `currentPlan` may keep `auto_generate=true` only after this endpoint refuses to invent a country-blind week. Until Phase 2 has an American pool, `US` follows the empty-state rule in that phase.

## Tests

- Null market plus auto-generate does not insert a plan.
- `vi` and Vietnam timezones default to `VN` once, on read of a null field, and a later `US` choice sticks.
- `en` plus `America/Los_Angeles` stays null.
- A `VN` generation request cannot select a catalog row whose cuisine is `japanese` or `korean`.

## Done when

A new account is not given a mixed week in the background. A Vietnamese-language account defaults to Vietnam. Everyone else sees the two-country choice before any meals are created.
