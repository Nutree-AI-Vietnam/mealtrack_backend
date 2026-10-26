# Subscription Access Cutover and Reconciliation

RevenueCat stays the purchase rail for the App Store and Google Play: the app
buys and restores through the RevenueCat SDK, logged in as the Firebase uid.
There is no direct App Store or Play server verification in the backend.
PostgreSQL owns the access decision behind two flags (Render environment,
read at request time, default off):

| Flag | On | Off |
|---|---|---|
| `SUBSCRIPTION_ACCESS_ENABLED` | `GET /v1/me/entitlements` answers from the database | The mobile kill switch: the endpoint answers `503 ENTITLEMENTS_DISABLED` and the app uses its RevenueCat client decision |
| `SUBSCRIPTION_ENFORCEMENT_ENABLED` | Paid AI routes answer `402 SUBSCRIPTION_REQUIRED` without access (needs the flag above too) | No server-side gate |

Webhooks, `POST /v1/me/entitlements/refresh` and this runbook's script write
verified RevenueCat facts whatever the flags say. MoMo (web checkout, a
separate change) writes its own `provider='momo'` rows into the same table;
nothing here reads or writes them. HTTP contract and store policy:
`docs/api-endpoints.md` and `docs/external-services.md`.

## The script

`scripts/reconcile_subscription_access.py` re-reads users from RevenueCat and
applies each customer with the same sync the webhooks use. Dry run unless
`--apply`; the dry run applies every snapshot inside a transaction it rolls
back, so it predicts the apply exactly and writes nothing.

| Population | Who is read |
|---|---|
| (default) `--subscribers` | Active users with at least one `provider='revenuecat'` row |
| `--repair [--expiring-within-hours N] [--modified-since T]` | Users with an unverified grant, a row a failed webhook read left unresolved, a deadline within N hours of now either side (default 24, `0` turns it off), or (with T) a row changed since T |
| `--user-id <database-user-id>` | One user |
| `--all-users` | Every active user. Opt-in only: RevenueCat's `GET /v1/subscribers` is get-or-create, so this creates an empty RevenueCat customer for every user who never bought |

Deleted and deactivated accounts are never read (their Firebase uid is a
tombstone).

- **Where:** from the repository root with the target environment's
  `APP_DATABASE_URL` and `REVENUECAT_SECRET_API_KEY`: a Render Shell on
  `mealtrack-backend` (`cd /app`) already has both, or an operator machine
  with them exported. A Render Shell ends when you disconnect, so use
  `--checkpoint` on long passes and resume.
- **Duration:** about `candidates x --min-interval-seconds` (default one
  RevenueCat request per second) plus retry waits; 5,000 subscribers take
  about 85 minutes. The first output line prints `candidates=N`.
- **Output:** `--output` gets one JSONL record per user: internal `user_id`,
  `decision` (`granted`, `denied`, `unresolved` after the run), `before`
  (the access policy's decision before the run), `legacy_before` (the
  decision users get today while the flag is off: the legacy row check
  sign-in uses), `reason`, `snapshot` (`ok`, `absent`, `unavailable`,
  `not_read`), `downgrade` (policy: granted before, denied after),
  `loses_current_access` (`legacy_before` granted, denied after), row
  counts. No emails, Firebase uids or provider payloads. Downgrades
  (`downgrade`), current-access losses (`loses_access`), unresolved users and
  failed users (`failed`) are also printed. The files are created `0600`; keep them off tickets and
  repositories and delete them after the review. On `--resume` the file is
  appended to; the last record per user wins.
- **Summary and exit code:** the last line is
  `RESULT: GO|NO-GO|INCOMPLETE mode=... users=... granted=... denied=...
  unresolved=... downgraded=... loses_current_access=... failed=...
  skipped=...`. `failed` counts live users whose customer could not be read
  (after the retries for transient failures) or whose snapshot could not be
  applied: the run did not verify them, even when their stored decision
  reads granted or denied. Exit `0` = GO (no user left unresolved and none
  failed), `1` = NO-GO (unresolved or failed users remain; only ever a
  verdict), `2` = usage or configuration error (including a refused
  checkpoint), `3` = incomplete: stopped early (RevenueCat asked to wait
  longer than `--max-wait-seconds`, five provider failures in a row, an
  interrupt) or crashed anywhere (database, checkpoint or output file, the
  `--gate` query, a bug); continue with `--resume`.
- **Checkpoint:** `--checkpoint PATH` records the mode, the population and
  the start time. `--resume` refuses a checkpoint from the other mode, from
  another population, or older than 24 hours; start a fresh pass instead.
  Give the dry run and the apply different paths.
- **RevenueCat limits:** `Retry-After` (and 429 backoff) holds every
  following request, not only the retried one.

| `reason` | Meaning | Action |
|---|---|---|
| `applied`, `unchanged`, `absent`, `stale_skipped` | Snapshot applied (or already current) | None |
| `absent_with_history` | RevenueCat holds no purchases under this Firebase uid, but the database holds a possible grant (fallback match, manual grant) | Find the purchase in RevenueCat and attach it to the Firebase uid (transfer), then `--user-id <id> --apply`. If nothing is owed, expire the row explicitly and note why. Never merge accounts by email |
| `owner_changed`, `conflicting_facts` | The user or RevenueCat changed during the read | Re-run `--user-id <id>`; escalate if it persists |
| `transport`, `http_429`, `http_5xx` | RevenueCat unavailable | `--resume` later |
| `malformed`, `unsupported_store`, `http_4xx`, `api_key_missing` | Contract or configuration problem | Engineering; also reported to Sentry by the app |
| `user_inactive`, `user_missing` | The account is deleted or gone (only with `--user-id`, or deleted during the run); nothing to verify, not counted as failed | None |
| `no_firebase_uid` | A live user with no Firebase uid cannot be read; counted as failed | Engineering: find how the account lost its uid |

## Cutover checklist (in order)

Rehearse steps 1-7 on staging, then run them on production. Step 3 needs the
RevenueCat sandbox, so it runs on staging only; steps 8 and 9 are
production-only.

### 1. Migrate before deploying the image

Every user load also loads the user's subscriptions, so the new image reads
the new columns whatever the flags say. Against an unmigrated database,
`POST /v1/users/sync` (sign-in), profile, onboarding, chat, progress and
RevenueCat webhooks fail with `UndefinedColumn`, while `/health` stays green
and Render shifts traffic anyway.

- [ ] Actions → **Migrate Database** → "Use workflow from" the branch or
      tag that contains these migrations (the release ref you deploy; the
      workflow runs the migrations of the ref it is dispatched from) → the
      environment, `dry_run=true`. The pending revisions include
      `20261022125800304190` (access metadata), `20261026080322454705` (sync
      marker) and `20261026085224333692` (nullable RevenueCat id).
- [ ] Run again with `dry_run=false` (production needs Environment
      approval).
- [ ] Run `dry_run=true` once more and read the **database** line: it must
      show `Current revision: 20261026085224333692` (or a later revision)
      followed by `Database is up to date`. `Head revision:` comes from the
      checked-out code and reads the same whether or not the upgrade ran, so
      it proves nothing.
- [ ] Preprod (`render-preprod.yml`) runs with the production environment,
      database included: do not deploy this image to preprod before
      production is migrated.

The migrations only add columns or relax a constraint: the previous image
keeps working on the migrated schema, so an image rollback never needs a
schema downgrade.

### 2. Deploy with both flags off

- [ ] Render env: `SUBSCRIPTION_ACCESS_ENABLED=false`,
      `SUBSCRIPTION_ENFORCEMENT_ENABLED=false` (the defaults).
- [ ] Deploy the image. `GET /v1/me/entitlements` answers
      `503 ENTITLEMENTS_DISABLED`, sign-in and profile keep the legacy
      decision, and webhooks start stamping rows with verified snapshots.

### 3. Verify webhooks against the RevenueCat sandbox (staging)

With staging migrated and deployed, its `REVENUECAT_SECRET_API_KEY` set and
the RevenueCat staging webhook pointing at it:

- [ ] Buy a subscription with a sandbox account (App Store sandbox, a Play
      test track, or RevenueCat Web sandbox) on a test user. Its row has
      `provider='revenuecat'`, `provider_snapshot_at` set and
      `provider_sync_required_at` NULL:

  ```sql
  SELECT s.status, s.product_id, s.expires_at, s.provider_snapshot_at,
         s.provider_sync_required_at
  FROM subscriptions AS s
  WHERE s.user_id = '<database-user-id>' AND s.provider = 'revenuecat';
  ```

- [ ] Cancel it: `status='cancelled'`, still granted until `expires_at`.
      Let it lapse (sandbox periods are minutes): `status='expired'`.
- [ ] Logs show `RevenueCat access synced: ... result=applied` and no
      `RevenueCat snapshot unavailable`.
- [ ] Turn the staging flag on and check step 7's requests for the test user
      (`product_id`, `will_renew`, `provider='revenuecat'`).

### 4. Reconcile dry run and review

```bash
python -m scripts.reconcile_subscription_access \
  --output ~/reconcile/dry-run.jsonl \
  --checkpoint ~/reconcile/dry-run.checkpoint
```

The dry run predicts the state after the apply, so its verdict is the one
the apply will reach. Review the per-user file:

```bash
jq -r '.reason' ~/reconcile/dry-run.jsonl | sort | uniq -c
jq -c 'select(.decision == "unresolved")' ~/reconcile/dry-run.jsonl
jq -c 'select(.loses_current_access or .downgrade)' ~/reconcile/dry-run.jsonl
```

- [ ] Every `unresolved` user has a plan (reason table above).
- [ ] Users losing access (`loses_current_access`: granted by today's
      legacy check, denied by RevenueCat; `downgrade`: the same under the
      policy for rows already verified) are mostly churned users whose
      `EXPIRATION` was missed: spot-check a sample in the RevenueCat
      dashboard before applying. At the first cutover every legacy grant is
      unverified, so these users show up only as `loses_current_access`
      (`before` reads `unresolved`). A large count, or paying users among
      them, points at a product or entitlement mapping problem: stop and
      investigate instead of applying.
- [ ] Buyers with no row at all (a lost `INITIAL_PURCHASE`) are not in this
      population; the app's refresh after purchase or restore, their next
      webhook, or an explicit `--all-users` pass repairs them.

### 5. Apply

```bash
python -m scripts.reconcile_subscription_access --apply \
  --output ~/reconcile/apply.jsonl \
  --checkpoint ~/reconcile/apply.checkpoint
```

- [ ] Exit `3`: run the same command with `--resume` (within 24 hours).
- [ ] Exit `1`: work through the unresolved and failed users (reason table
      above), then `--user-id <id> --apply` for each.

### 6. SQL go/no-go gate

Run against the target database (Neon SQL editor or `psql`). Both counts
must be `0`:

```sql
SELECT
  count(*) FILTER (
    WHERE s.status IN ('active', 'cancelled', 'billing_issue')
      AND s.provider_snapshot_at IS NULL
  ) AS unverified_grants,
  count(*) FILTER (
    WHERE s.provider_sync_required_at IS NOT NULL
  ) AS sync_required
FROM subscriptions AS s
JOIN users AS u ON u.id = s.user_id
WHERE s.provider = 'revenuecat'
  AND u.is_active
  AND u.deleted_at IS NULL;
```

`python -m scripts.reconcile_subscription_access --gate` runs the same query
and exits `0` only on `GATE: GO`.

- [ ] `unverified_grants=0`: no RevenueCat grant that no snapshot verified.
- [ ] `sync_required=0`: no row a failed webhook read left unresolved. If
      not, run `--repair --apply` and check again.

The users behind a non-zero count:

```sql
SELECT s.user_id, s.status, s.provider_snapshot_at, s.provider_sync_required_at
FROM subscriptions AS s
JOIN users AS u ON u.id = s.user_id
WHERE s.provider = 'revenuecat'
  AND u.is_active
  AND u.deleted_at IS NULL
  AND (
    (s.status IN ('active', 'cancelled', 'billing_issue')
     AND s.provider_snapshot_at IS NULL)
    OR s.provider_sync_required_at IS NOT NULL
  );
```

### 7. Turn the access flag on and verify

- [ ] Render env: `SUBSCRIPTION_ACCESS_ENABLED=true`; it takes effect when
      Render redeploys the service.
- [ ] For known test users (paying, lapsed, never paid), with a Firebase ID
      token:

  ```bash
  curl -sS -H "Authorization: Bearer $ID_TOKEN" \
    https://<api-host>/v1/me/entitlements
  curl -sS -X POST -H "Authorization: Bearer $ID_TOKEN" \
    https://<api-host>/v1/me/entitlements/refresh
  ```

  Both answer `200` with the expected `has_access`, `status`, `expires_at`,
  `product_id`, `will_renew` and `provider`.
- [ ] Watch the monitoring signals below for at least a day:
      `entitlements_unavailable` and `legacy_fallback` stay near zero.

### 8. Staged mobile rollout

- [ ] Only now release the app build that reads `GET /v1/me/entitlements`,
      as a staged rollout (App Store phased release, Play staged rollout),
      checking the monitoring signals between stages.
- [ ] That build keeps buying and restoring through the RevenueCat SDK
      (logged in as the Firebase uid), calls `POST /v1/me/entitlements/refresh`
      after a purchase or restore, and never treats a `503` as a denial
      (`ENTITLEMENTS_DISABLED` → RevenueCat decision; `ENTITLEMENTS_UNAVAILABLE`
      → retry, last known decision, or RevenueCat).

### 9. Enforcement, later and separately

- [ ] Turn `SUBSCRIPTION_ENFORCEMENT_ENABLED=true` only after the mobile
      rollout is complete and the database decisions have matched RevenueCat
      for a sustained period. It makes the paid AI routes answer `402`
      (list in `docs/api-endpoints.md`); a wrong database row then blocks a
      paying user server-side. Both flags are read from the service's
      environment, so turning one off takes effect when Render redeploys
      after the env change (a few minutes), not at once.

## Rollback

**Flag off (any time).** Set `SUBSCRIPTION_ACCESS_ENABLED=false`. It takes
effect when Render redeploys after the env change (a few minutes); until
then the old value keeps serving. The app then falls back to RevenueCat on
`503 ENTITLEMENTS_DISABLED`, enforcement stops with it, and webhooks keep
stamping rows.

1. Note the UTC time the flag went off.
2. Before turning it back on, repair the window it was off, then pass the
   gate:

   ```bash
   python -m scripts.reconcile_subscription_access --repair --apply \
     --modified-since <time-off> --output ~/reconcile/repair.jsonl
   python -m scripts.reconcile_subscription_access --gate
   ```

**Image rollback (to a build before this change).** The old webhook handlers
insert rows without a snapshot stamp (a first purchase becomes `active` with
`provider_snapshot_at` NULL) and change verified rows without touching
`provider_snapshot_at`, the grace deadline or the period type.

An image before this change has no `/v1/me/entitlements` routes: it answers
`404 {"detail": "Not Found"}`, never `503 ENTITLEMENTS_DISABLED`. Once the
step 8 mobile build is live, such a rollback relies on the app treating a
`404` (or any non-`200` without a known `error_code`) like
`ENTITLEMENTS_DISABLED` (`docs/api-endpoints.md`). Confirm the released
build does; otherwise roll back only to an image that still serves the
routes and use the flag alone.

1. Turn `SUBSCRIPTION_ACCESS_ENABLED` (and enforcement) off first and note
   the UTC time.
2. Roll back the image. Keep the schema; the old image runs on it.
3. After rolling forward, run the repair above with `--modified-since` set to
   the rollback start, without reusing an older checkpoint (the script
   refuses one older than 24 hours). It selects every row the old image
   inserted or changed.
4. Pass the step 6 gate, then turn the flag on and repeat step 7.

Downgrade the migrations only after no running image reads the columns, and
know that the schema downgrade loses row ownership. Downgrading
`20261026085224333692` fills every NULL `revenuecat_subscriber_id` (rows of
other providers) with the owner's Firebase uid; downgrading
`20261022125800304190` drops `provider`. After both, a MoMo row reads as a
RevenueCat row that RevenueCat code may expire or re-point. Once
`provider='momo'` rows exist, do not downgrade past `20261022125800304190`
(or export those rows first and restore them after rolling forward).

## Scheduled repair (recommended)

Run the repair hourly so a webhook whose RevenueCat read failed beyond
RevenueCat's retries (about 5 over 80 minutes), or a missed renewal or
expiration near its deadline, does not wait for the user's next event:

```bash
cd /app && python -m scripts.reconcile_subscription_access --repair --apply \
  --expiring-within-hours 24
```

- Exit `1` (users left unresolved, or reads that failed) or `3` (stopped
  early) should alert; the stdout lists the unresolved and failed internal
  user ids with their reasons.
- Keep one pass well inside the interval: candidates x one second. Lower
  `--expiring-within-hours` if it grows. Overlapping passes are safe (writers
  for one user serialise on the user row) but waste RevenueCat quota.
- **Open decision (owner: backend lead), not yet scheduled.** This
  repository does not define the job, because it is new paid
  infrastructure. Either add a Render Cron Job on the same image and
  environment as `mealtrack-backend` (schedule `0 * * * *`, the command
  above), or a scheduled GitHub Actions workflow on an Environment holding
  `APP_DATABASE_URL` and `REVENUECAT_SECRET_API_KEY`. Get approval for the
  cost first.
- Until it exists, nothing in the backend clears a `provider_sync_required_at`
  marker on its own. The repair is client-driven: the app calls
  `POST /v1/me/entitlements/refresh` after a purchase or restore and on
  `ENTITLEMENTS_UNAVAILABLE`, which resolves the marker for that user; users
  who do not open the app stay unresolved (sign-in and paid routes use the
  legacy decision meanwhile). Run the repair above by hand after any
  RevenueCat incident, and check the step 6 gate before each flag change.

## Monitoring

Application logs plus Sentry metrics and issues, emitted through the
observability facade:

| Signal | Meaning | Alert on |
|---|---|---|
| Metric `subscription_access.snapshot_unavailable` (`source`, `failure_kind`, `event_type`), WARNING `RevenueCat snapshot unavailable; access left unresolved for repair` | A webhook (`source=webhook`), refresh or web finalize (`source=web_funnel`) could not read or apply a RevenueCat customer. Only webhooks mark rows for repair; a refresh or finalize writes nothing. The reconcile script emits nothing here: its failures are its exit code and stdout | Any `failure_kind` in `malformed`, `unsupported_store`, `http_4xx`, `api_key_missing`, `unexpected` (also a Sentry exception): page. Transient kinds (`transport`, `http_429`, `http_5xx`) are not sent to Sentry; alert on a sustained rate of this counter (e.g. more than 20 in 15 minutes) |
| Metric `subscription_access.entitlements_unavailable` (`operation`, `failure_kind`) | `GET` or refresh answered `503 ENTITLEMENTS_UNAVAILABLE` | `failure_kind=unexpected` (logged with `logger.exception` and sent to Sentry): page on any. `unresolved`: users on unrepaired rows; should be near zero after cutover |
| Metric `subscription_access.legacy_fallback` (`source`), WARNING `Subscription access is unresolved in the database; using the legacy decision` | Sign-in, profile or a paid route fell back to the legacy row check | Should trend to zero after step 6; investigate if it persists |
| Metric `subscription_access.downgrade` (`source`, `event_type`, `result`, `expected`), Sentry `RevenueCat sync removed access on an unexpected event` | A sync removed access the user had before the event. `expected=true` on an `EXPIRATION`, a refund (a `CANCELLATION` too, so only this attribute separates the two) or the source of a `TRANSFER` (it loses what RevenueCat moved away); `expected=false` otherwise, including a `TRANSFER` target that loses access, also reported to Sentry | `expected=false` more than 5 per hour (entitlement or product misconfiguration in RevenueCat) |
| Sentry `RevenueCat customer has entitlements but no 'standard'` | RevenueCat entitlement configuration drift | Page |
| WARNING `RevenueCat snapshot older than stored facts skipped`, metric `subscription_access.sync` with `result=stale_skipped` | Out-of-order reads; routine when the app's refresh after a purchase commits before the purchase webhook. Not sent to Sentry | Spikes only |
| Metric `subscription_access.sync` (`source`, `event_type`, `result`) | Every applied snapshot | Volume drops to zero (webhooks not arriving) |
| Metric `webhook.revenuecat.processed` (`event_type`, `status`, `result`) | Webhook outcomes; `result=access_unresolved` means the rows were marked for repair, `result=account_deleted` an event for a deleted account (legacy effects only, nothing synced) | Rate of `access_unresolved`, and webhook HTTP 5xx at Render (RevenueCat stops retrying after about 5 attempts) |
| Metric `subscription_access.enforcement_denied` | A paid route answered `402` | A spike right after step 9: turn enforcement off |
| Scheduled repair exit code | `1` users left unresolved or reads that failed, `3` stopped early | Any failed run |
