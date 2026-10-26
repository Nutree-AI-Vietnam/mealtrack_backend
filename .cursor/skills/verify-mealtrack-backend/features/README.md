# MealTrack backend verification map

This directory is the maintained source for verifying client-facing HTTP behavior. Read it before driving the API, then use the matching feature file.

## Baseline preconditions

- Launch with `.cursor/skills/verify-mealtrack-backend/scripts/verify.sh launch`.
- `$BASE` is the `base:` line from `doctor` (default `http://127.0.0.1:8000`).
- `$EVIDENCE` is the `evidence:` line from `doctor`.
- `doctor` exits 0. Do not drive an instance this run did not start.
- Authenticated calls send no `Authorization` header. The process injects the dev user.
- Send `X-Timezone: UTC` on writes that take a date.

## Driving conventions

- Start from the baseline unless the feature adds a precondition.
- Treat every curl as literal. Keep paths, headers, and JSON unchanged.
- Record status and body under `$EVIDENCE/<feature>/`.
- A write is proved by a later read of the same value, plus the database row named in the feature.
- Restore mutated rows before finishing the feature. Cleanup drops the database; the feature still puts the row back.

## Proof and skip reporting

- Capture the request and the resulting state.
- Report an unreachable path with the command and the unmet precondition.
- Do not mark a skipped entry point verified through a different path.
- AI scan, parse-text, chat, barcode, RevenueCat, and Firebase ID-token routes are outside this map until credentials exist.

## Feature entry contract

Each feature file has an H1, one paragraph, then four H2 sections: `Sub-features`, `How to get to it (user POV)`, `Driving it with curl`, `Gotchas`.

## Features

- [Hydration](./hydration.md) — catalog, log water, read it back, delete it.
- [Weight](./weight.md) — add a weight entry, list it, delete it.
- [Movement](./movement.md) — catalog, log a walk, read the day, delete it.
- [TDEE preview](./tdee-preview.md) — onboarding calorie preview with no account and no write.
- [Notification preferences](./notification-preferences.md) — read defaults, change breakfast time, read it back.
