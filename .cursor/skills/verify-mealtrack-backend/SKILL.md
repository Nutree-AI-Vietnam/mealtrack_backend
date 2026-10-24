---
name: verify-mealtrack-backend
description: Drive the MealTrack FastAPI HTTP API the way the mobile client does. Use when booting the local Compose stack and proving hydration, weight, movement, TDEE preview, or notification preferences against the running server.
---

# Verify MealTrack backend

The surface is the versioned HTTP API. This repo has no web UI. The mobile client is the user. Verification boots Postgres and Redis from `docker-compose.yml`, loads the schema, and serves `src.api.main:app` with `uv`. It then calls the same routes the client calls.

One stack only. Compose pins containers `mealtrack_postgres` and `mealtrack_redis`, host ports 5432 and 6379, and volumes `mealtrack_postgres_data` and `mealtrack_redis_data`. Do not start a second instance. If those containers exist without the `mealtrack.verify=1` label, stop. They are not this run.

Authenticated routes use the local bypass (`ENVIRONMENT=development` and `ENABLE_DEV_AUTH_BYPASS=1`). Send no `Authorization` header. That is how `scripts/development/local.sh` talks to the API. It does not prove a Firebase ID token.

## Launch

From the repo root:

```bash
.cursor/skills/verify-mealtrack-backend/scripts/verify.sh launch
```

`launch` refuses a busy port 8000, 5432, or 6379, and refuses compose containers it did not label. It then:

1. `docker compose up -d --wait` until Postgres and Redis are healthy.
2. `uv python install 3.13.2`, `uv sync --frozen`, then `scripts/init_postgres_db.py` and `alembic upgrade head` on `postgresql://nutree:nutree@127.0.0.1:5432/nutree`. A new volume is empty.
3. Writes a throwaway service-account JSON under `/tmp/mealtrack-verify/`. Startup calls `firebase_admin.initialize_app`, and this repo ships no Firebase credentials. The file is scaffolding, not a Firebase project. Cleanup deletes it.
4. Starts one uvicorn process on `127.0.0.1:8000` (override with `VERIFY_PORT` before launch).

Ready means the log contains `MealTrack API started successfully!` and `GET /v1/health/db-connections` returns `"status":"healthy"`. `GET /health` does not query Postgres, so it is not enough.

`launch` prints `base:` and `evidence:`. `doctor` prints the same lines. Export them before driving:

```bash
BASE=$(.cursor/skills/verify-mealtrack-backend/scripts/verify.sh doctor | awk -F': ' '/^base:/ {print $2}')
EVIDENCE=$(.cursor/skills/verify-mealtrack-backend/scripts/verify.sh doctor | awk -F': ' '/^evidence:/ {print $2}')
```

## Doctor

```bash
.cursor/skills/verify-mealtrack-backend/scripts/verify.sh doctor
```

Read-only. Exit 0 only when the recorded pid is alive and owns the port, `GET /health` is healthy, `GET /` returns `"name":"MealTrack API"`, `GET /v1/health/db-connections` with this run's `X-Monitoring-Token` is healthy, both containers are healthy and labeled `mealtrack.verify=1`, and the process is on the local auth bypass. Run it before driving when a call looks wrong.

## Drive

Read `features/README.md`, then the feature file. Run its curls against `$BASE`. Save bodies under `$EVIDENCE/<feature>/`. Check the status and the follow-up read, not only the write response. Send `X-Timezone: UTC` on dated writes.

Do not call meal scan, parse-text, chat, barcode, RevenueCat, or real Firebase-token routes. Those need secrets this skill does not have. A missing key is an unmet precondition, not a pass.

## Evidence

`$EVIDENCE` is `.cursor/skills/verify-mealtrack-backend/artifacts/<utc-run-id>/`. `launch` writes `launch.txt` (ready log line, `/health`, db-connections). Feature files name the rest. Git ignores `artifacts/`.

## Cleanup

```bash
.cursor/skills/verify-mealtrack-backend/scripts/verify.sh cleanup
```

Kills the recorded pid and its children. Runs `docker compose down -v` only when both containers carry `mealtrack.verify=1`. Deletes `/tmp/mealtrack-verify`, including the Firebase scaffolding. Leaves `$EVIDENCE` in place.

## Helpers

`scripts/verify.sh` takes `launch`, `doctor`, or `cleanup`.

When the API changes, run `/maintain-verification-skill` so this map stays honest.
