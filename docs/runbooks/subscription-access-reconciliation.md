# Subscription Access Cutover and Reconciliation

RevenueCat stays the purchase rail for the App Store and Google Play: the app
buys and restores through the RevenueCat SDK, logged in as the Firebase uid.
There is no direct App Store or Play server verification in the backend.
PostgreSQL owns the access decision behind two flags (Render environment,
read at request time, default off):

| Flag | On | Off |
|---|---|---|
| `SUBSCRIPTION_ACCESS_ENABLED` | `GET /v1/me/entitlements` answers from the database | The mobile kill switch: the endpoint answers `503 ENTITLEMENTS_DISABLED` and the app uses its RevenueCat client decision |
| `SUBSCRIPTION_ENFORCEMENT_ENABLED` | Paid AI routes answer `402 SUBSCRIPTION_REQUIRED` without access (needs the flag above too) | No server-side gate from this flag. The Ghi nhanh describe routes keep their own always-on Premium check, which reads neither flag (`docs/api-endpoints.md`) |

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
  skipped=... gone=...`. `failed` counts live users whose customer could not
  be read (after the retries for transient failures) or whose snapshot could
  not be applied: the run did not verify them, even when their stored
  decision reads granted or denied. `gone` counts deleted, deactivated or
  vanished accounts (also one deleted during the run): only there, never in
  `granted`/`denied`/`unresolved`, and outside the verdict, as the `--gate`
  SQL ignores them too. Exit `0` = GO (no user left unresolved and none
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
| `absent_with_history` | RevenueCat holds no purchases under this Firebase uid, but the database holds a possible grant (fallback match, manual grant). Not reported for the source of a `TRANSFER` whose own read failed: the webhook remembers it (`provider_transfer_source_at`), and the next read after the transfer applies the emptied customer. A `TRANSFER` delivery lost after RevenueCat's retries leaves the source's row verified and unmarked, so only a `--subscribers` pass shows it, with this reason | Find the purchase in RevenueCat and attach it to the Firebase uid (transfer), then `--user-id <id> --apply`. If nothing is owed (it moved to another account), expire the row explicitly and note why. Never merge accounts by email |
| `owner_changed`, `conflicting_facts` | The user or RevenueCat changed during the read | Re-run `--user-id <id>`; escalate if it persists |
| `transport`, `http_429`, `http_5xx` | RevenueCat unavailable | `--resume` later |
| `malformed`, `unsupported_store`, `http_4xx`, `api_key_missing` | Contract or configuration problem | Engineering; also reported to Sentry by the app |
| `user_inactive`, `user_missing` | The account is deleted or gone (only with `--user-id`, or deleted during the run, also between its read and the apply); nothing to verify. Counted in `gone` only, never as unresolved or failed | None |
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
      marker), `20261026085224333692` (nullable RevenueCat id),
      `20261028084517715547` (merge with delivery's scan-feedback head, no
      schema change), `20261028085152877654` (`refunded` status value) and
      `20261028090710086261` (transfer source marker).
- [ ] Run again with `dry_run=false` (production needs Environment
      approval).
- [ ] Run `dry_run=true` once more and read the **database** line: it must
      show `Current revision: 20261028090710086261` (or a later revision)
      followed by `Database is up to date`. `Head revision:` comes from the
      checked-out code and reads the same whether or not the upgrade ran, so
      it proves nothing.
- [ ] Check that `subscriptions.status` accepts `refunded`, which refund
      syncs write. A database built from the models (`create_all`, as
      production's was) has a `VARCHAR` column; one built by the Alembic
      chain has the native enum migration 007 created, which only
      `20261028085152877654` extends. Against a native enum without the value,
      every refund webhook answers `500` and rolls back.

  ```sql
  SELECT data_type, udt_name
  FROM information_schema.columns
  WHERE table_schema = current_schema()
    AND table_name = 'subscriptions' AND column_name = 'status';
  -- Only when udt_name is subscription_status_enum:
  SELECT enum_range(NULL::subscription_status_enum);
  ```

  Go when the first query returns `character varying`, or the second lists
  `refunded`. Otherwise stop: the migration did not run on this database.
- [ ] Preprod (`render-preprod.yml`) runs with the production environment,
      database included: do not deploy this image to preprod before
      production is migrated.

The migrations only add columns, add an enum value or relax a constraint:
the previous image keeps working on the migrated schema, so an image rollback
never needs a schema downgrade.

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
- [ ] Rehearse a refund: refund a RevenueCat Web sandbox purchase, or POST
      a `CANCELLATION` event with `cancel_reason=CUSTOMER_SUPPORT` for the
      test user to the staging webhook (with its `Authorization` header).
      The webhook answers `200` (a `500` means the column check in step 1
      failed), the logs show `RevenueCat refund received; referral clawback
      is disabled`, the referrer's `referral_wallets` row is unchanged
      (refund clawback is off: `docs/external-services.md`, "Refunds and
      referral commission"), and the row follows RevenueCat: `refunded` once
      RevenueCat no longer grants, unchanged when the posted event refunded
      nothing.
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
      webhook, or an explicit `--all-users` pass repairs them. Step 9 needs
      that `--all-users` pass.

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
      → retry, last known decision, or RevenueCat). A `404` or any other
      non-`200` without a known `error_code` reads like
      `ENTITLEMENTS_UNAVAILABLE`; the image rollback below relies on it.

### 9. Enforcement, later and separately

Turn `SUBSCRIPTION_ENFORCEMENT_ENABLED=true` only when all of these hold. It
makes the paid AI routes answer `402` (list in `docs/api-endpoints.md`); a
wrong database row then blocks a paying user server-side. Before a `402` the
route asks RevenueCat (cut off after 3 s in total), which rescues a payer the
database does not know, but not while RevenueCat is down or slow. It asks
at most once per user per minute, whatever the answer: a payer refused less
than a minute before buying is rescued by the first request after that
minute, or at once after the app's refresh, a synced webhook or a
web-funnel finalize.

- [ ] The mobile rollout is complete and the database decisions have
      matched RevenueCat for a sustained period.
- [ ] One `--all-users --apply` pass has run since step 7 and ended `GO`
      (on exit `1`, work through its unresolved and failed users as in
      step 5 first).
      The `--subscribers` and `--repair` passes and the step 6 gate only see
      users who already have a RevenueCat row; paying users with none (a
      lost or anonymous purchase webhook, a promotional grant, an event type
      the old image ignored) only show up in an all-users pass. It creates an
      empty RevenueCat customer for every user who never bought (see the
      population table), so plan its duration (users x one second):

  ```bash
  python -m scripts.reconcile_subscription_access --all-users --apply \
    --output ~/reconcile/all-users.jsonl \
    --checkpoint ~/reconcile/all-users.checkpoint
  ```

- [ ] The share of active installs on app builds older than step 8's (from
      App Store Connect and Play Console version reports) is near zero.
      Those builds never call `POST /v1/me/entitlements/refresh` and show a
      `402` as a generic server error, so a payer the database denies while
      RevenueCat cannot answer stays blocked there until their next webhook.
- [ ] Both flags are read from the service's environment, so turning one off
      takes effect when Render redeploys after the env change (a few
      minutes), not at once. Watch `enforcement_denied` and
      `enforcement_revenuecat_grant` (Monitoring) after turning it on.

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
`provider_snapshot_at`, the grace deadline or the period type. They also
answer `200` to `UNCANCELLATION`, `SUBSCRIPTION_EXTENDED`,
`TEMPORARY_ENTITLEMENT_GRANT`, `NON_RENEWING_PURCHASE`, `REFUND_REVERSED`,
`SUBSCRIPTION_PAUSED` and any newer type without writing anything:
RevenueCat counts those delivered and never resends them, and they leave no
trace in the database.

An image before this change has no `/v1/me/entitlements` routes: it answers
`404 {"detail": "Not Found"}`, never `503 ENTITLEMENTS_DISABLED`. Once the
step 8 mobile build is live, such a rollback relies on the app treating a
`404` (or any non-`200` without a known `error_code`) like
`ENTITLEMENTS_UNAVAILABLE`, never as a denial (`docs/api-endpoints.md`): a
purchase it just made, else its last backend decision for that user while
that still grants (unexpired, read at most 72 hours ago), else RevenueCat,
else access unknown. Confirm the released build behaves this way (the app's
`docs/contracts.md`, "Subscription access"); otherwise roll back only to an
image that still serves the routes and use the flag alone.

No `200` decision reaches the app once step 1 below takes effect. For a
grant only the database holds (a `momo` web pass, or any other grant
RevenueCat cannot see), the rollback runs like this:

- From step 1 taking effect until the old image serves traffic, the routes
  answer `503 ENTITLEMENTS_DISABLED`: RevenueCat decides, so the grant is
  denied. The app keeps its stored decision from the last `200`; the kill
  switch does not replace it.
- After step 2 the old image answers `404`, which the app reads as
  unavailable. That stored decision (read before step 1) grants again while
  it is unexpired and at most 72 hours old, then ages out. RevenueCat then
  denies the grant again, as under the flag alone, until the image is rolled
  forward and the flag is back on. A device without such a decision is
  denied throughout.
- The same stored decision can undo a revocation RevenueCat showed during
  the flag-off window (a refund before `expires_at`, say): after step 2 that
  user is granted again until the decision ages out.

So avoid an image rollback while `momo` or other database-only grants
exist, or accept the gap: those users lose access from step 1, may get it
back after step 2 for what remains of the 72 hours since their last `200`,
and lose it again until the flag is back on after rolling forward. The flag
alone denies them too while it is off, but without the return after step 2
or the undone revocations.

1. Turn `SUBSCRIPTION_ACCESS_ENABLED` (and enforcement) off first and note
   the UTC time.
2. Roll back the image. Keep the schema; the old image runs on it.
3. After rolling forward, run a full pass over every RevenueCat subscriber,
   not the `--modified-since` repair: `updated_at` shows the rows the old
   image inserted or changed, but not the users whose events it
   acknowledged without writing. Start fresh (no older checkpoint; the
   script refuses one older than 24 hours):

   ```bash
   python -m scripts.reconcile_subscription_access --subscribers --apply \
     --output ~/reconcile/rollforward.jsonl \
     --checkpoint ~/reconcile/rollforward.checkpoint
   ```

   It reads every user holding a RevenueCat row (about one second each).
   A buyer with no row at all whose only event in the window was one of
   the types above is not in it; the app's refresh after purchase or
   restore, their next webhook, or an `--all-users` pass repairs them.
4. Pass the step 6 gate, then turn the flag on and repeat step 7.

Downgrade the migrations only after no running image reads the columns, and
know what each downgrade loses. Downgrading `20261026080322454705` drops
`provider_sync_required_at`: rows a failed webhook read marked for repair
lose the marker, so after rolling forward the facts that event's handler
wrote read as verified on a row an earlier snapshot stamped. Before downgrading it, run
`--repair --apply` until `--gate` shows `sync_required=0`, or accept the
loss and run the full `--subscribers --apply` pass above after rolling
forward. Downgrading `20261028090710086261` drops
`provider_transfer_source_at`: a transfer source still marked then answers
`absent_with_history` instead of resolving itself; run `--repair --apply`
until `--gate` shows `sync_required=0` first. Downgrading
`20261028085152877654` changes nothing (PostgreSQL keeps an enum value).
The schema downgrade also loses row ownership. The image before this change
looks RevenueCat rows up by `revenuecat_subscriber_id` alone, with no
`provider` filter, so it would expire or overwrite any row carrying a user's
Firebase uid. Downgrading `20261026085224333692` therefore refuses (it
raises `Refusing to downgrade 20261026085224333692` and changes nothing)
while a row of another provider has no `revenuecat_subscriber_id`; it fills
only RevenueCat rows that lack one. Downgrading `20261022125800304190` then
drops `provider`. Once `provider='momo'` rows exist, do not downgrade past
`20261026085224333692`: export those rows and delete them first, and restore
them after rolling forward, or keep the schema (the default above).

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
- Until it exists, a `provider_sync_required_at` marker clears only when a
  RevenueCat snapshot taken at or after it is applied for that user: on the
  user's next webhook whose RevenueCat read succeeds, on the app's
  `POST /v1/me/entitlements/refresh` (after a purchase or restore and on
  `ENTITLEMENTS_UNAVAILABLE`), or on a reconcile or repair run. Users with
  neither a later RevenueCat event nor an app open stay unresolved
  meanwhile: sign-in and profile use the legacy decision (an `active`,
  unexpired row), and paid routes under enforcement grant any `active`,
  `cancelled` or `billing_issue` row that is unexpired or in grace, so a
  payer whose `CANCELLATION` or `BILLING_ISSUE` read failed keeps them. A
  refund whose read failed reads `cancelled` there too (the cancellation
  handler wrote it) until a snapshot revokes it. Run the repair above by
  hand after any RevenueCat incident, and check the step 6 gate before each
  flag change.

## Monitoring

Application logs plus Sentry metrics and issues, emitted through the
observability facade:

| Signal | Meaning | Alert on |
|---|---|---|
| Metric `subscription_access.snapshot_unavailable` (`source`, `failure_kind`, `event_type`), WARNING `RevenueCat snapshot unavailable; access left unresolved for repair` | A webhook (`source=webhook`), refresh or web finalize (`source=web_funnel`) could not read or apply a RevenueCat customer. Only webhooks mark rows for repair; a refresh or finalize writes nothing. The reconcile script emits nothing here: its failures are its exit code and stdout | Any `failure_kind` in `malformed`, `unsupported_store`, `http_4xx`, `api_key_missing`, `unexpected` (also a Sentry exception): page. Transient kinds (`transport`, `http_429`, `http_5xx`) are not sent to Sentry; alert on a sustained rate of this counter (e.g. more than 20 in 15 minutes) |
| Metric `subscription_access.entitlements_unavailable` (`operation`, `failure_kind`) | `GET` or refresh answered `503 ENTITLEMENTS_UNAVAILABLE` | `failure_kind=unexpected` (logged with `logger.exception` and sent to Sentry): page on any. `unresolved`: users on unrepaired rows; should be near zero after cutover |
| Metric `subscription_access.legacy_fallback` (`source`), WARNING `Subscription access is unresolved in the database; using the legacy decision` | Sign-in or profile fell back to the legacy row check, or a paid route to the enforcement fallback (`docs/api-endpoints.md`) | Should trend to zero after step 6; investigate if it persists |
| Metric `subscription_access.downgrade` (`source`, `event_type`, `result`, `expected`), Sentry `RevenueCat sync removed access on an unexpected event` | A sync removed access the user had before the event. `expected=true` on an `EXPIRATION`, a refund (a `CANCELLATION` too, so only this attribute separates the two) or the source of a `TRANSFER` (it loses what RevenueCat moved away); `expected=false` otherwise, including a `TRANSFER` target that loses access, also reported to Sentry | `expected=false` more than 5 per hour (entitlement or product misconfiguration in RevenueCat) |
| Sentry `RevenueCat customer has entitlements but no 'standard'` | RevenueCat entitlement configuration drift | Page |
| WARNING `RevenueCat snapshot older than stored facts skipped`, metric `subscription_access.sync` with `result=stale_skipped` | Out-of-order reads; routine when the app's refresh after a purchase commits before the purchase webhook. Not sent to Sentry | Spikes only |
| Metric `subscription_access.sync` (`source`, `event_type`, `result`) | Every applied snapshot | Volume drops to zero (webhooks not arriving) |
| Metric `webhook.revenuecat.processed` (`event_type`, `status`, `result`) | Webhook outcomes; `result=access_unresolved` means the rows were marked for repair, `result=account_deleted` an event for a deleted account (legacy effects only, nothing synced) | Rate of `access_unresolved`, and webhook HTTP 5xx at Render (RevenueCat stops retrying after about 5 attempts) |
| Metric `subscription_access.enforcement_denied` (`revenuecat`: `not_entitled` or `unavailable`; `source`: `revenuecat`, `recent_check` or `not_asked`) | A paid route answered `402`. `revenuecat` is the answer of the RevenueCat check before it; `source=recent_check` means that answer was held from the user's check in the last minute (or another worker's call was still in flight, read as `unavailable`), `not_asked` that the user has no Firebase uid or RevenueCat is not configured | A spike right after step 9: turn enforcement off. `revenuecat=unavailable` means payers may be among them |
| Metric `subscription_access.enforcement_revenuecat_grant`, WARNING `Paid route allowed by RevenueCat; the database grants no access` | Enforcement found no granting row but RevenueCat itself answered that it grants: a purchase not yet (or never) synced | A sustained rate: find the users in the logs and `--user-id <id> --apply` them; check webhook delivery |
| Metric `subscription_access.enforcement_cached_grant` (`source`), INFO `Paid route allowed by a cached grant` | Enforcement found no granting row but the Premium check's 60 s access cache held a grant, which may be that check's own row decision (`require_subscription` caches both) rather than RevenueCat's | Informational; compare with `enforcement_revenuecat_grant` before acting on either |
| Scheduled repair exit code | `1` users left unresolved or reads that failed, `3` stopped early | Any failed run |
