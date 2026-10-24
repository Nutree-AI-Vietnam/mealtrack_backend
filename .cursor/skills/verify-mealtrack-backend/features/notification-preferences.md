# Notification preferences

Notification preferences let the client read reminder times and change the breakfast reminder. The first read creates the default row when none exists.

## Sub-features

- `notifications-read` returns the default breakfast time of 510 minutes.
- `notifications-update` sets breakfast to 480 minutes.
- `notifications-reread` returns 480 from a second GET.
- `notifications-restore` puts breakfast back to 510.

## How to get to it (user POV)

- `GET /v1/notifications/preferences`
- `PUT /v1/notifications/preferences`

## Driving it with curl

Preconditions:

- `doctor` exits 0.
- No preference row yet, or the current breakfast time is still 510. A fresh launch satisfies this.

- **Read.** `mkdir -p "$EVIDENCE/notification-preferences" && curl -sS -D "$EVIDENCE/notification-preferences/get.headers" -o "$EVIDENCE/notification-preferences/get.json" -w "%{http_code}\n" "$BASE/v1/notifications/preferences"`. Status `200`. `breakfast_time_minutes` is `510`.
- **Update.** `curl -sS -D "$EVIDENCE/notification-preferences/put.headers" -o "$EVIDENCE/notification-preferences/put.json" -w "%{http_code}\n" -X PUT "$BASE/v1/notifications/preferences" -H 'Content-Type: application/json' -d '{"breakfast_time_minutes":480}'`. Status `200`. `success` is true and `preferences.breakfast_time_minutes` is `480`.
- **Read again.** GET into `$EVIDENCE/notification-preferences/get-after.json`. `breakfast_time_minutes` is `480`.
- **Stored row.** `docker exec mealtrack_postgres psql -U nutree -d nutree -tAc "SELECT breakfast_time_minutes FROM notification_preferences"`. Prints `480`.
- **Restore.** PUT `{"breakfast_time_minutes":510}` into `$EVIDENCE/notification-preferences/restore.json`. The following GET shows `510`.

## Gotchas

- The first GET inserts defaults when the user has no row. A read of 510 on a fresh database is that insert, not a sign the update already happened.
- `breakfast_time_minutes` is minutes from midnight, 0–1439. 510 is 08:30. 480 is 08:00.
- Only fields sent in the PUT change. This journey sends breakfast only.
- Push token registration (`POST /v1/notifications/tokens`) is a different call and is not this journey.
