# Movement

Movement lets the client list activities, log a walk, see it on the daily summary, and delete it.

## Sub-features

- `movement-catalog` includes `walking`.
- `movement-log` stores a 30-minute moderate walk.
- `movement-daily` returns that entry.
- `movement-delete` removes it.

## How to get to it (user POV)

- `GET /v1/movement/catalog`
- `POST /v1/movement/log`
- `GET /v1/movement/daily`
- `DELETE /v1/movement/{entry_id}`

## Driving it with curl

Preconditions:

- `doctor` exits 0.
- `X-Timezone` is `UTC`.

- **Catalog.** `mkdir -p "$EVIDENCE/movement" && curl -sS -D "$EVIDENCE/movement/catalog.headers" -o "$EVIDENCE/movement/catalog.json" -w "%{http_code}\n" "$BASE/v1/movement/catalog"`. Status `200`. `activities` contains an object with `id` `walking`.
- **Log.** `curl -sS -D "$EVIDENCE/movement/log.headers" -o "$EVIDENCE/movement/log.json" -w "%{http_code}\n" -X POST "$BASE/v1/movement/log" -H 'Content-Type: application/json' -H 'X-Timezone: UTC' -d '{"activity_id":"walking","activity_name":"Walking","duration_min":30,"kcal_burned":150,"intensity":"moderate","include_in_balance":true}'`. Status `201`. Body `activity_id` is `walking`, `duration_min` is `30`, `kcal_burned` is `150`, `intensity` is `moderate`.
- **Daily read.** `curl -sS -D "$EVIDENCE/movement/daily.headers" -o "$EVIDENCE/movement/daily.json" -w "%{http_code}\n" -H 'X-Timezone: UTC' "$BASE/v1/movement/daily"`. Status `200`. `entries` contains that `id`.
- **Stored row.** `docker exec mealtrack_postgres psql -U nutree -d nutree -tAc "SELECT duration_min FROM movement_entries WHERE id = '<id>'"`. Prints `30`.
- **Delete.** `curl -sS -D "$EVIDENCE/movement/delete.headers" -o "$EVIDENCE/movement/delete.json" -w "%{http_code}\n" -X DELETE "$BASE/v1/movement/<id>"`. Status `204` and an empty body.
- **Gone.** Daily read into `$EVIDENCE/movement/daily-after.json` has no that `id`. The same `psql` prints no row.

## Gotchas

- `intensity` is `light`, `moderate`, or `hard`. `walking` supports all three.
- `kcal_burned` must be between 0 and `duration_min * 30`, and at most 5000. `150` for 30 minutes fits. A larger number is a real rejection, not a flaky assertion.
- Omit `X-Timezone: UTC` and the entry can miss `GET /daily`.
- Catalog `name` values are per-language objects. Match `id`, not a display string.
