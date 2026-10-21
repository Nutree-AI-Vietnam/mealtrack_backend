# TDEE preview

TDEE preview is the onboarding calculator. The client sends body stats and reads calorie and macro targets. The call does not require a signed-in user and does not store a profile.

## Sub-features

- `tdee-preview` returns BMR, TDEE, and macros for one metric recomp profile.
- `tdee-stable` repeats the same request and gets the same calorie target.
- `tdee-no-write` leaves the `users` row count unchanged.

## How to get to it (user POV)

- `POST /v1/tdee/preview`

## Driving it with curl

Preconditions:

- `doctor` exits 0.
- No `Authorization` header. This route does not use the dev user.

- **Count users.** `docker exec mealtrack_postgres psql -U nutree -d nutree -tAc "SELECT COUNT(*) FROM users"` and save the number.
- **Preview.** `mkdir -p "$EVIDENCE/tdee-preview" && curl -sS -D "$EVIDENCE/tdee-preview/preview.headers" -o "$EVIDENCE/tdee-preview/preview.json" -w "%{http_code}\n" -X POST "$BASE/v1/tdee/preview" -H 'Content-Type: application/json' -d '{"age":25,"sex":"male","height":180,"weight":75,"body_fat_percentage":15,"job_type":"desk","training_days_per_week":4,"training_minutes_per_session":60,"goal":"recomp","unit_system":"metric","diet_type":"classic"}'`. Status `200`. `goal` is `recomp`. `bmr`, `tdee`, and `macros.calories` are numbers greater than 0. `macros` also has `protein`, `carbs`, and `fat`.
- **Repeat.** Run the same POST into `$EVIDENCE/tdee-preview/preview-again.json`. `macros.calories` matches the first body.
- **No write.** The `users` count equals the number from the first step.

## Gotchas

- The route is limited to 10 requests per minute per IP. Two calls are the journey. A 429 means the limit was hit, which fails the run.
- Height for `metric` must be 100–272 cm and weight 30–250 kg. The body above is inside that range.
- Do not assert a hardcoded calorie number. Assert the two responses match and the fields are present.
- This does not prove `POST /v1/user-profiles/metrics`, which persists a profile.
