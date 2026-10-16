# Entitlement Ledger Runbook (NM-680)

Use this runbook to roll out, operate and roll back the backend entitlement
ledger: RevenueCat webhook → `billing_provider_events` (inbox) → RevenueCat
re-fetch → `store_subscriptions` → `user_entitlements` (projection) →
`GET/POST /v1/me/entitlements`.

Design: `plans/261015-1200-nm680-backend-entitlement-source-of-truth/plan.md`.
HTTP rules: `docs/api-endpoints.md` (Entitlements). Failure policy:
`docs/external-services.md`.

## Moving parts

| Part | Where |
|---|---|
| Inbox + inline sync | `POST /v1/webhooks/revenuecat` (`src/api/routes/v1/webhooks.py`) |
| Sync (only writer of store rows + projection) | `src/app/services/entitlement_sync_service.py` |
| Inbox processing, replay | `src/app/services/entitlement_inbox_service.py` |
| Client answers | `src/app/services/entitlement_account_service.py`, `src/api/routes/v1/entitlements.py` |
| Worker | `python -m scripts.entitlement_worker` (`src/infra/workers/entitlement_worker.py`) |
| Backfill | `python -m scripts.backfill_entitlement_ledger` |
| RC budget | `src/infra/services/rc_budget.py` (per process) |

## Flags

Rows in the `feature_flags` table, flipped with the admin
`PUT /v1/feature-flags/{name}`. A missing row or a read error means **off**.
Each process caches flags for 30 s, so a flip takes effect within 30 s.
`ent_*` names never appear in `GET /v1/feature-flags`, and
`GET /v1/feature-flags/{name}` answers 404 for them. Announce every `ent_*`
change in the release channel.

| Flag | Effect when ON | Safe to turn off? |
|---|---|---|
| `ent_ledger_write` | Webhook writes an inbox row inside the legacy transaction, then syncs inline (3 s budget). Lookup uses billing identities first and active users only; deleted accounts are parked (200) and no longer written by the legacy path; settled duplicates are acknowledged without legacy work. `/v1/users/sync` seeds the primary identity. | Yes: legacy path only. |
| `ent_reconcile` | Worker runs. Webhook answers 200 and parks unknown users (`unmatched`) instead of 404. `/v1/users/sync` replays the user's parked rows. | Yes: unknown users get 404 again, so RC retries resume. |
| `ent_read_api` | `/v1/me/entitlements` answers `authority: "backend"`; `/v1/users/sync` and `GET /v1/users/firebase/{uid}` read `has_subscription` from the projection. | Yes. |
| `ent_read_api_defer` | Kill switch: forces `authority: "rc"` (and the legacy `has_subscription`) even with `ent_read_api` on. `POST /sync` stops calling RC. | It is the rollback. |

## Settings

| Setting | Default | Notes |
|---|---|---|
| `ENTITLEMENT_SANDBOX_POLICY` | `all` | `all` / `allowlist` / `none` (D3), case-insensitive; any other value fails startup. |
| `ENTITLEMENT_SANDBOX_ALLOWED_USER_IDS` | empty | Comma-separated `users.id` for `allowlist`. |
| `ENTITLEMENT_RC_RPS` | `2.0` | Per process. Web has 4 uvicorn workers, so about 4 × this hits RC. |
| `ENTITLEMENT_RC_CONCURRENCY` | `4` | Per process. |
| `REVENUECAT_SECRET_API_KEY` | – | Used by the ledger provider and the legacy adapter. |

## Rollout order

1. **Migrate** (expand): apply `add_entitlement_ledger_tables` and
   `add_billing_event_raw_retention_index` through the manual migration workflow (`docs/runbooks/schema-migration.md`). Deploy the
   code. All flags stay off; nothing changes.
2. **P1 – write**: turn on `ent_ledger_write`. Watch for 7 days:
   `webhook.revenuecat.non_2xx` (must not rise), `webhook.revenuecat.ledger_sync{outcome}`
   (≥ 99 % `processed`), `entitlement.mismatch{direction}` (explain every one),
   `identity.alias_conflict`.
3. **P2 – worker**: start one worker process (below) while `ent_reconcile` is
   still off; it idles. Turn on `ent_reconcile`. Watch
   `entitlement.worker.heartbeat`, `entitlement.inbox.failed{outcome=dead}`,
   `entitlement.inbox.lag`.
4. **Backfill**: dry run, review the report, then the real run (below).
   Any user active in RC but not in the ledger blocks the next step.
5. **Read API**: turn on `ent_read_api`. Clients still decide from RC until the
   app's PostHog flag `entitlement_source` moves to `shadow`, then `backend`.
6. **Rollback at any point**: `ent_read_api_defer` on (immediate, server side),
   then investigate.

## Worker

```bash
python -m scripts.entitlement_worker                 # loop until SIGTERM
python -m scripts.entitlement_worker --once          # one pass, prints JSON counts
```

- Idle unless `ent_reconcile` is on. Run **one** instance: inbox rows are
  claimed with `FOR UPDATE SKIP LOCKED` and a 600 s lease, but the periodic
  re-sync lists are not partitioned between instances.
- Each pass: due inbox rows (`received`/`retry`, expired leases; a new
  webhook row is due 30 s after receipt, so the worker only finishes what the
  inline sync did not, and the inline path skips rows the worker holds); every 60 s,
  projections whose `next_change_at` passed (DB only) and active users with
  access whose last RC sync is older than 24 h (a user whose re-sync fails is
  skipped for 1 h, so failures cannot fill every batch); every 15 min, parked
  rows whose user now resolves; every hour, stored webhook bodies (`raw`)
  older than 90 days are nulled in batches (D9, `entitlement.inbox.raw_purged`).
- Failed rows back off exponentially (30 s doubling, capped at 6 h) and go
  `dead` after 10 attempts.
- It shares `ENTITLEMENT_RC_RPS` with nothing in its own process; budget the
  RC rate across web + worker.

## Backfill

```bash
# 1. Diff report only: no ledger writes, no checkpoint
python -m scripts.backfill_entitlement_ledger --dry-run --limit 500 \
  --report backfill-dry.jsonl
# 2. Real run, resumable
python -m scripts.backfill_entitlement_ledger --rps 0.5 \
  --checkpoint backfill.checkpoint.json --report backfill.jsonl
python -m scripts.backfill_entitlement_ledger --resume --rps 0.5 \
  --checkpoint backfill.checkpoint.json --report backfill.jsonl
```

- Candidates: active users with a Firebase UID, ordered by `users.id`, plus
  known legacy RC ids (`users.revenuecat_customer_id`,
  `subscriptions.revenuecat_subscriber_id`), which are seeded as `legacy_lead`
  identities and fetched on every later sync. Only known ids are fetched (RC
  v1 GET is get-or-create).
- Only lead UUIDs and `$RCAnonymousID:*` values are seeded, never an id that
  is any account's Firebase UID or another user's identity: legacy `TRANSFER`
  handling left the target's UID on the source user's row. A legacy customer
  that RevenueCat also lists under another account is skipped at every sync,
  and a legacy customer never takes a store row from another user.
- A RevenueCat error is retried after `--provider-retry-wait` (default 30 s,
  the circuit-breaker window) up to `--provider-retries` times (default 3).
  If it still fails, the run stops with `stopped_at_user_id` and does not
  checkpoint that user; rerun with `--resume` once RC is healthy.
- Uses the `backfill` lane, its own budget (`--rps`, default 0.5) and the
  production sync service. No queue events, referral credit or notifications.
- Report rows: `outcome` = `match` / `mismatch` / `provider_error` /
  `discarded` / `user_not_found` / `error`, with `rc_active`, `ledger_before`,
  `ledger_after`, `legacy_active` and notes such as `legacy_differs`.
  Run time ≈ users ÷ rps (100k users at 1 rps ≈ 28 h).

## Inbox triage

```sql
-- Backlog by status
SELECT status, park_reason, count(*), min(received_at)
FROM billing_provider_events GROUP BY 1, 2 ORDER BY 1;
-- Dead rows and why
SELECT id, event_type, app_user_id, attempts, last_error, received_at
FROM billing_provider_events WHERE status = 'dead' ORDER BY received_at DESC LIMIT 50;
-- Requeue dead rows after fixing the cause (the worker picks them up)
UPDATE billing_provider_events
SET status = 'retry', attempts = 0, next_attempt_at = now(), last_error = NULL
WHERE status = 'dead' AND received_at > now() - interval '7 days';
-- D9 retention by hand while no worker runs (ent_reconcile off): repeat until 0
UPDATE billing_provider_events SET raw = NULL
WHERE id IN (SELECT id FROM billing_provider_events
             WHERE raw IS NOT NULL AND received_at < now() - interval '90 days'
             ORDER BY received_at LIMIT 1000 FOR UPDATE SKIP LOCKED);
```

- **Parked (`unmatched`)**: `user_not_found` rows replay automatically when the
  user signs in (`/v1/users/sync`), when RevenueCat redelivers the event after
  the user exists, or when the worker sweep resolves them.
  `deleted_user` rows stay parked by design (D11).
- **One user looks wrong**: `GET /v1/admin/entitlements/{user_id}` shows the
  projection, store rows, grants and last 20 inbox rows. A user's
  `POST /v1/me/entitlements/sync` with a hint forces a re-fetch.
- **RC outage / 429**: syncs keep the last projection (`stale: true`); inbox
  rows go to `retry`. `rc.429`, `rc.circuit.open` and
  `entitlement.sync.provider_error{code}` show it. Once RC is back the worker
  drains the retries. Failed webhook deliveries (non-2xx) can be resent from
  the RC dashboard.

## Manual grants (D5)

```bash
curl -X POST "$API/v1/admin/entitlements/grants" -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"user_id": "<users.id>", "source": "support", "reason": "ticket 123",
       "ends_at": "2026-11-15T00:00:00Z", "source_ref": "ticket-123"}'
curl -X POST "$API/v1/admin/entitlements/grants/<grant_id>/revoke" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"reason": "issued by mistake"}'
```

Admins need an `ADMIN_EMAILS` address with a verified email and a
non-custom-token sign-in. Admin tokens are revocation-checked: disabling the
Firebase user or `firebase_admin.auth.revoke_refresh_tokens(uid)` ends access
at once. Web-funnel silent login marks a lead's email verified without proving
mailbox control (it refuses only addresses already in `ADMIN_EMAILS`), so
before adding an address, and once for the current list, check its Firebase
record (`auth.get_user_by_email`): a password provider added after a
web-funnel sign-in, or a record the funnel created, means revoke its refresh
tokens and have the mailbox owner sign in again. Grants are capped at 366 days and must end within
366 days of being issued (chained grants cannot extend that; issue a new one
later). They are never mirrored to RC in this build, so old app builds (RC
path) do not see them.

## Rollback

| Problem | Action |
|---|---|
| Clients get wrong answers | `ent_read_api_defer` on (or `ent_read_api` off). |
| Webhook errors rising | `ent_ledger_write` off: legacy path only, inbox rows stop. |
| Worker misbehaving | Stop the process or turn `ent_reconcile` off (unknown users get 404 again, RC retries). |
| Schema | Turn every flag off, stop the worker and roll the code back first; only then run the migration's `downgrade()`, which drops the five tables. If tables are missing while this code runs (rolled back first, or deployed before the migration), account deletion still succeeds: its ledger step (retire identities, null stored bodies) is best-effort and counts `entitlement.delete_hook.failed` instead. |
