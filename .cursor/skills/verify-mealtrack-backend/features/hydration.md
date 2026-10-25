# Hydration

Hydration lets the client list drinks, log a water entry, see that volume on the daily summary, and remove the entry.

## Sub-features

- `hydration-catalog` lists drinks, including `water`.
- `hydration-log` stores a 250 ml water entry.
- `hydration-daily` shows that entry and its credited volume.
- `hydration-delete` removes it from the daily summary and the database.

## How to get to it (user POV)

- `GET /v1/hydration/catalog`
- `POST /v1/hydration/log`
- `GET /v1/hydration/daily`
- `DELETE /v1/hydration/{entry_id}`

## Driving it with curl

Preconditions:

- `doctor` exits 0.
- `X-Timezone` is `UTC`.

- **Catalog.** `mkdir -p "$EVIDENCE/hydration" && curl -sS -D "$EVIDENCE/hydration/catalog.headers" -o "$EVIDENCE/hydration/catalog.json" -w "%{http_code}\n" "$BASE/v1/hydration/catalog"`. Status `200`. The body has a drink `id` `water`, `name` `Water`, `category` `hydration`.
- **Log water.** `curl -sS -D "$EVIDENCE/hydration/log.headers" -o "$EVIDENCE/hydration/log.json" -w "%{http_code}\n" -X POST "$BASE/v1/hydration/log" -H 'Content-Type: application/json' -H 'X-Timezone: UTC' -d '{"drink_id":"water","volume_ml":250}'`. Status `201`. Body `drink_id` is `water`, `volume_ml` is `250`, `credited_ml` is `250`, and `id` is non-empty. Keep that `id`.
- **Daily read.** `curl -sS -D "$EVIDENCE/hydration/daily.headers" -o "$EVIDENCE/hydration/daily.json" -w "%{http_code}\n" -H 'X-Timezone: UTC' "$BASE/v1/hydration/daily"`. Status `200`. `entries` contains that `id` with `volume_ml` 250, and `consumed_ml` is at least 250.
- **Stored row.** `docker exec mealtrack_postgres psql -U nutree -d nutree -tAc "SELECT volume_ml FROM hydration_entries WHERE id = '<id>'"`. Prints `250`.
- **Delete.** `curl -sS -D "$EVIDENCE/hydration/delete.headers" -o "$EVIDENCE/hydration/delete.json" -w "%{http_code}\n" -X DELETE "$BASE/v1/hydration/<id>"`. Status `200` and body `success` is true.
- **Gone.** Repeat the daily read into `$EVIDENCE/hydration/daily-after.json`. `entries` has no that `id`. The same `psql` prints no row.

## Gotchas

- `POST /log` accepts hydration drinks only (`water`, `sparkling`, `tea`, `coffee`, `coke-zero`). Caloric ids use `POST /v1/hydration/log/drink`, which this journey does not cover.
- Volume must be 1–2000. `250` matches the catalog default for water.
- Omit `X-Timezone: UTC` and the entry can fall on a different local day than `GET /daily`.
- The first authenticated call inserts dev user `dev@example.com`. That row is the bypass user, not a client sign-up.
- Queue credentials are unset. The log must still return `201`. A missing queue message is not a failure. The daily read and the `hydration_entries` row are the proof.
- A write response alone is not proof.
