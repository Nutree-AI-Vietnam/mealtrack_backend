# Weight

Weight lets the client store a body-weight sample, see it in history, and delete it.

## Sub-features

- `weight-add` stores 70.5 kg.
- `weight-list` returns that sample.
- `weight-delete` removes it from the list and the database.

## How to get to it (user POV)

- `POST /v1/weight-entries`
- `GET /v1/weight-entries`
- `DELETE /v1/weight-entries/{entry_id}`

## Driving it with curl

Preconditions:

- `doctor` exits 0.
- No entry is already stored at `2026-10-21T15:04:00+00:00` for the dev user. A fresh launch satisfies this.

- **Add.** `mkdir -p "$EVIDENCE/weight" && curl -sS -D "$EVIDENCE/weight/add.headers" -o "$EVIDENCE/weight/add.json" -w "%{http_code}\n" -X POST "$BASE/v1/weight-entries" -H 'Content-Type: application/json' -d '{"weight_kg":70.5,"recorded_at":"2026-10-21T15:04:00+00:00"}'`. Status `200`. Body `weight_kg` is `70.5` and `id` is non-empty.
- **List.** `curl -sS -D "$EVIDENCE/weight/list.headers" -o "$EVIDENCE/weight/list.json" -w "%{http_code}\n" "$BASE/v1/weight-entries"`. Status `200`. `entries` contains that `id` with `weight_kg` `70.5`.
- **Stored row.** `docker exec mealtrack_postgres psql -U nutree -d nutree -tAc "SELECT weight_kg FROM weight_entries WHERE id = '<id>'"`. Prints `70.5`.
- **Delete.** `curl -sS -D "$EVIDENCE/weight/delete.headers" -o "$EVIDENCE/weight/delete.json" -w "%{http_code}\n" -X DELETE "$BASE/v1/weight-entries/<id>"`. Status `200` and `deleted` is true.
- **Gone.** List again into `$EVIDENCE/weight/list-after.json`. That `id` is absent. The same `psql` prints no row.

## Gotchas

- `(user, recorded_at)` is unique. Repeating the same timestamp without deleting returns an error. Do not change the timestamp to hide that.
- `weight_kg` must be greater than 0.
- The list is the proof. The add response is not.
