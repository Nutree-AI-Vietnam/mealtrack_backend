# Backend External Services

**Status:** Evergreen integration policy (fail-fast vs degrade)  
**Config owner:** `src/infra/config/settings.py` and `.env.example`  
**Adapter owner:** `src/infra/adapters/`, `src/infra/services/`, `src/infra/cache/`, `src/infra/monitoring/`  
**Incident response:** `docs/runbooks/provider-outage.md` and sibling runbooks

Do not treat this file as a provider HOW-TO or env inventory. Discover keys from
settings, wiring from adapters, and live health from OpenAPI `/docs`.

---

## Failure policy matrix

| Dependency | Class | On failure |
|------------|-------|------------|
| PostgreSQL | **Required** | Fail fast (typically 503) |
| Firebase Auth | **Required** | Fail fast (401) |
| AI provider stack | **Required for AI routes** | Circuit breaker → next model in chain; exhausted chain raises `AIUnavailableError` |
| Cloudflare Workers AI | **Optional routed provider** | Skip / fall through; disable via env |
| RevenueCat | **Payment facts** | Webhook/reconciliation sync updates PostgreSQL; the database policy owns runtime access. A provider failure never blocks webhook side effects: the user's rows are left unresolved (entitlements answer 503) and reported for repair |
| Redis optional caches | **Optional** | Bypass cache; continue from source of truth |
| Redis provider budget | **Required for provider-origin manual writes** | Fail closed with `NUTRITION_PROVIDER_UNAVAILABLE` |
| Redis meal-suggestion sessions | **Required transient state** (current design) | Session writes fail when store unavailable |
| Cloudflare Queue integration | **Required** | Direct post-commit publication; Queue errors are surfaced to the caller or webhook retry path |
| Upstash Redis REST for Worker deletes | **Required for the Queue consumer** | Worker retries, then DLQ, on Redis/network/delete failures |
| OpenAI translation | **Optional read-path localization** | Return canonical local/provider results without translated names; do not block startup or writes |
| OpenAI, FatSecret, USDA, OFF, Brave, image stock APIs | **Optional enrichment** | Degrade to local/prior results when safe |
| Sentry | **Optional observability** | Local logs only; facade no-ops without DSN |
| nutree-affiliate | **Optional partner** | Validate may return inactive; lifecycle events retry via outbox |

**Redis rule:** optional caches are never the source of truth for nutrition,
notification preferences, user state, or write-path correctness. Cache
admission policy: `docs/decisions/260608-2223-selective-cache-admission-policy.md`.
Provider-origin v2 manual saves use Redis and `NUTRITION_PROVIDER_GLOBAL_RPM`
(default **60** if unset or empty). `ENVIRONMENT=development` may use a
process-local budget if Redis is down so local catalog-food saves work.
`CACHE_ENABLED=false` may disable optional cache reads, but it must not
disable this shared write-path budget.

**Privacy rule:** never log prompts, food payloads, raw AI output, base64
images, emails, auth tokens, full barcodes, or secrets. Prefer operation name,
internal IDs, status codes, and error class.

**Cloudflare async integration rule:** hydration is the current direct-publish
path. The backend commits the hydration row first, then publishes
`hydration.created.v1` directly to the required environment ingress Queue. The
Worker orchestrator invokes registered handlers in order and ACKs only after
all handlers succeed. A failure retries the whole ingress message and
eventually reaches the ingress DLQ. There is no HMAC, no local-vs-Cloudflare
dual routing, no percentage canary, no D1 delivery ledger, and no cache-value
write path in this slice. The accepted MVP trade-off is that a post-commit
Queue failure can lose the hydration event; only the committed database row is
durable.

---

## Adapter map (WHERE)

| Concern | Owner paths |
|---------|-------------|
| Settings / env | `src/infra/config/settings.py` |
| Firebase Auth + FCM | `src/infra/services/firebase_service.py`, `firebase_auth_service.py`, `src/infra/services/push/` |
| Cloudflare images and meal-photo R2 | `src/infra/adapters/cloudflare_image_store.py`, `r2_object_store.py` (`docs/cloudflare-images-guide.md`) |
| AI routing / circuit breaker | `src/infra/services/ai/` (`ai_model_manager.py`, providers, adapters) |
| Vision analysis | `src/infra/adapters/vision_ai_service.py` |
| OpenAI prompt-cache policy | `src/infra/services/ai/openai_prompt_cache_policy.py` |
| Food providers (USDA, FatSecret, OFF, Brave) | `src/infra/adapters/food_data_service.py`, `fat_secret_service.py`, `open_food_facts_service.py`, `brave_search_nutrition_service.py` |
| Translation | `src/infra/adapters/openai_translation_adapter.py` |
| Cloudflare Queue publisher | `src/infra/adapters/cloudflare_queue_publisher.py` |
| Stock / generated images | `pexels_image_adapter.py`, `unsplash_image_adapter.py`, `imagen_image_generator.py`, `pollinations_image_generator.py`, `cloudflare_workers_image_generator.py` |
| RevenueCat | `src/infra/adapters/revenuecat_adapter.py`; normalized writes in `src/infra/services/subscription_access_sync_service.py`; webhook entry in `src/api/routes/v1/webhooks.py` with sibling modules `webhook_access_sync.py`, `webhook_subscription_lifecycle.py`, `webhook_referral_funnel.py`, `webhook_lookup_parsing.py` |
| Web funnel redemption | `src/infra/services/web_funnel_*` |
| Resend email | `src/infra/adapters/resend_email_adapter.py` |
| Redis | `src/infra/cache/` |
| Queue consumer | sibling `nutreeai_async` repository |
| Sentry / observability facade | `src/infra/monitoring/`, `src/observability.py` |
| Affiliate outbox client | `src/infra/adapters/affiliate_service_adapter.py`; dispatch trigger removed — see `docs/decisions/ADR-cron-jobs-removal.md` |
| DB pool | `src/infra/database/config_async.py`, `connection_policy.py` — see `database-guide.md` |

---

## Non-derivable integration rules

### AI provider routing

- Runtime registry is **OpenAI + optional Cloudflare Workers AI**. Gemini
  packages may remain in dependencies but are not registered as a runtime
  provider unless code changes that fact.
- `AIModelManager` owns per-purpose fallback chains and the circuit breaker
  (failures open the breaker; recovery is coded in the manager).
- Text and vision Cloudflare paths are independent (LangChain text vs REST
  vision). Vision model IDs and purpose lists are settings-owned — read
  `CLOUDFLARE_WORKERS_AI_*` from settings, not this doc.
- Optional graph workflow (`AI_MEAL_ANALYZE_GRAPH_ENABLED`) must not change
  provider order or READY meal API contracts.
- Optional FatSecret meal validation is never required for a valid scan.
- Prompt cache (OpenAI Responses via LangChain) is best-effort; keys hash
  system prompt only — never raw user text/images. Monitor facade metrics before
  claiming savings.
- Never log `[AI-*]` payloads beyond provider, model alias, purpose, status,
  error class.

### Translation ownership and privacy

- Read-path localization is owned by `src/app/services/food_name_localizer.py`
  and the translation services under `src/domain/services/translation/` and
  `src/domain/services/meal_*_translation_service.py`.
- The OpenAI translation adapter uses the Responses API with
  `store_responses=False`; the translation path never opts back into payload
  storage.
- Persisted meal translation rows written before the active translation version
  are treated as stale and excluded from responses until the next translation
  request rewrites them.
- Translation requests are bounded indexed batches. The adapter sends the
  indexed item text only, treats it as data rather than instructions, and
  preserves placeholders, units, and brands.
- `TranslationOutcome.TRANSLATED` is the only cache/persistence-eligible
  outcome. `PARTIAL`, `PASSTHROUGH`, and `UNAVAILABLE` fall back to canonical
  text and stay out of locale caches and persisted translation rows.

### Barcode cascade reliability

Order and verification rules (handlers/adapters own exact implementation):

1. Local `food_reference` / cache with GTIN aliases  
2. FatSecret exact barcode  
3. OpenFoodFacts exact barcode  
4. USDA FDC branded exact `gtinUpc` (when key present)  
5. Brave + AI as estimate hints only  
6. AI estimate last  

Invalid GTIN → 400 before external calls. Exact provider hits may be cached as
verified data. Brave-only / name-only / AI estimates return `is_estimate=true`
and must not enter the global catalog. Logs must not include full raw barcodes.

### Redis cache posture

| Data | Policy |
|------|--------|
| Food search/details, nutrition lookup, AI cost caches | Optional cache-aside; miss/error → source of truth |
| Auth UID mapping | Process-local TTL, not Redis |
| Notification rows / FCM ownership | Database only — do not cache as source of truth |
| Meal suggestion sessions | Required Redis-backed transient state today |

Default: do not cache. Admission checklist lives in the selective-cache decision
above. `FAIL_ON_CACHE_ERROR=false` is the expected production posture for
optional caches.

### Cloudflare async cache invalidation

- Non-hydration mutation paths publish versioned integration events directly to
  Cloudflare Queue after the database transaction commits. There is no active
  backend outbox relay for this path.
- Hydration creation publishes `hydration.created.v1` directly to Cloudflare
  Queue after its database transaction commits. The Worker orchestrator owns
  its cache translation and invokes the same delete handler.
- `CloudflareQueuePublisher.from_settings()` reads the Queue account, Queue ID,
  token, and timeout settings. The REST API requires the Queue ID; the
  human-readable Worker queue name is not a valid substitute. Blank Queue
  account/token fields fall back to `CLOUDFLARE_ACCOUNT_ID` and
  `CLOUDFLARE_API_TOKEN`; Queue-specific values override the generic values.
  `CLOUDFLARE_QUEUE_ID` remains required. If
  the fallback is used, the shared token must have both Queue and Workers AI
  permissions. Publication is always attempted; when misconfigured, the
  request fails after the database commit. The MVP accepts that post-commit
  failure window for hydration.
- The Worker validates the event envelope and deletes exact keys or bounded
  patterns through Upstash Redis REST. Queue ACK/retry/DLQ semantics own the
  delivery lifecycle.
- `HMAC`, revision tables/fencing, cache writes, local-vs-Cloudflare dual
  routing, and percentage canaries are intentionally out of scope for this
  slice.

### Cloudflare integration event orchestration

- The integration-event redesign uses one versioned `IntegrationEvent` as the
  ingress payload. Generic integration paths publish directly to the required
  Queue after commit. Web-funnel claim and reconciliation jobs retain their
  separate transactional outbox workflow.
- Hydration events carry the hydration aggregate ID rather than a snapshot.
  The hydration cache handler queries Neon for the hydration owner and local log
  date, so adding another event does not require another Worker parser.
- One ingress message owns the retry lifecycle. If a handler fails, earlier
  handlers may run again on retry, so handlers must be idempotent.
- Staging and production use separate ingress queues and matching
  `environment` values. The MVP does not use D1 or a dynamic subscription
  catalog.
- Configure one environment-specific Worker ingress queue name in
  `nutreeai_async/wrangler.jsonc` and its matching Cloudflare Queue ID as
  `CLOUDFLARE_QUEUE_ID` in the backend. Generic integration events and
  compatibility payloads share this queue during the migration.
- Hydration creation currently registers cache invalidation. Notification and
  email handlers remain follow-up work until their payload and idempotency
  contracts are explicit.

### Parse-text nutrition validation

- `/v1/meals/parse-text` keeps its authenticated and guest response contracts;
  structured local `food_reference` and staged FatSecret details are optional
  enrichment behind `PARSE_TEXT_STRUCTURED_REFERENCE_ENABLED` (default `false`).
- The handler derives calories from final macros. Provider detail calls are
  bounded to one selected candidate per item, with a request-wide deadline and
  call budget; invalid or ambiguous provider data falls back only when the AI
  estimate passes density checks.
- Run `python scripts/development/evaluate_parse_text_nutrition.py --mode offline`
  for the hermetic golden corpus. Live evaluation is staging-only, opt-in via
  `ENVIRONMENT=staging` and `PARSE_TEXT_LIVE_EVAL_ENABLED=true`, and requires
  explicit `--confirm-live-staging`; reports are synthetic-ID, aggregate-only,
  non-overwriting, and written with private `0600` permissions.

### RevenueCat and web redemption

- RevenueCat is the purchase rail for the App Store and Google Play: the app
  buys and restores through the RevenueCat SDK logged in as the Firebase uid,
  and renewals, billing issues, refunds and expirations reach the backend only
  through RevenueCat webhooks and snapshots. The backend has no direct App
  Store or Google Play verification (no receipt or purchase-token endpoint, no
  App Store Server Notifications or Play RTDN); dropping RevenueCat would need
  that whole store lifecycle stack first.
- RevenueCat verifies purchase facts. Runtime access is evaluated from verified
  `subscriptions` rows; `GET /v1/me/entitlements` never calls RevenueCat.
  `POST /v1/me/entitlements/refresh` is the per-user self-heal: it reads the
  caller's RevenueCat customer and applies it like a webhook would (contract
  and rate limit in `docs/api-endpoints.md`).
- `subscriptions.provider` names the row's owner; RevenueCat code reads and
  writes only `provider='revenuecat'` rows (rules for other providers below).
- `SUBSCRIPTION_ENFORCEMENT_ENABLED` (with `SUBSCRIPTION_ACCESS_ENABLED`) makes
  the paid AI routes answer 402 `SUBSCRIPTION_REQUIRED`; the route list is in
  `docs/api-endpoints.md`. The Ghi nhanh describe routes
  (`POST /v1/hydration/describe`, `POST /v1/movement/describe`) are separate:
  their own Premium check (`premium_check.require_subscription`: an unexpired
  `active` or `billing_issue` row of any provider, else RevenueCat within 3 s)
  is always on and reads neither flag.
- A failed or malformed provider fetch must not erase a previously verified
  row. Use the guarded reconciliation command described in
  `docs/runbooks/subscription-access-reconciliation.md` before enabling reads.
  No job repairs rows on a schedule: support resyncs single users through
  the admin API (`/v1/admin/subscription-access`, `docs/api-endpoints.md`),
  `GET /v1/health/subscription-access` and the Sentry rules in the
  runbook's "Monitoring" alert when rows a failed read left unresolved pile
  up or age, and the team then runs the script's `--repair` mode by hand.
- Webhooks (signature verification is constant-time):
  - Every event type whose user resolves is re-read from RevenueCat
    (`GET /v1/subscribers/{firebase_uid}`, only when the event names that
    uid) and applied to `provider='revenuecat'` rows; only `TEST`,
    `EXPERIMENT_ENROLLMENT`, `INVOICE_ISSUANCE` and
    `VIRTUAL_CURRENCY_TRANSACTION` are skipped. New event types sync by
    default.
  - The legacy handlers (rows, referral credit, lifecycle and affiliate
    events) always run, whatever `SUBSCRIPTION_ACCESS_ENABLED` is.
    If the customer cannot be read or applied, the webhook still answers 200;
    the user's RevenueCat rows get `provider_sync_required_at` (RevenueCat's
    event time), which the access policy treats as unresolved, and the failure
    is logged and counted (`subscription_access.snapshot_unavailable`, by
    `failure_kind`). It also goes to Sentry, except a RevenueCat outage or
    rate limit (`transport`, `http_429`, `http_5xx`), which would send one
    event per webhook; the counter carries that alert (runbook, Monitoring).
    A contract or configuration failure is sent under the fixed fingerprint
    `[subscription_access.snapshot_failure, <failure_kind>]` (event tag
    `alert`), which the runbook's issue alert matches; a webhook that
    answers `500` under `subscription_access.webhook_processing_error`.
    Only a snapshot taken at or after that time clears it (next event,
    refresh or reconciliation).
  - Writers for one user serialise on `SELECT ... FOR UPDATE` of the user row;
    an older snapshot never overwrites a newer one (`provider_snapshot_at`).
  - An event lands on the row of its own product (an old plan's
    `EXPIRATION` never on the newer plan's row). Google Play webhooks name
    `<subscription>:<base plan>` while snapshots name the bare subscription
    id, so row lookups and snapshot targets treat both forms as one product,
    the exact id first. A snapshot's target is a row of its own store before
    the exact id (an App Store product can share its id with a Play
    subscription), and the newest row on a remaining tie, like the lookup.
  - RevenueCat has no `REFUND` event: a refund is a `CANCELLATION` with
    `cancel_reason=CUSTOMER_SUPPORT` or a negative price. It runs the
    cancellation handler, as production always has: no referral clawback,
    and the `subscription_canceled` lifecycle event (see "Refunds and
    referral commission" below). Access follows the snapshot (a refund does
    not always end the subscription); rows the sync revokes for a refund read
    `refunded`, and the loss of access counts as expected. A refund whose
    customer cannot be read or applied also marks the user's rows with it
    (`provider_refund_pending_at`), so the paid-route enforcement fallback
    refuses them until a snapshot taken after the refund decides.
  - `TRANSFER` re-reads every local source and target user from its own
    customer; nothing is re-pointed or expired blindly. A source whose read
    fails is marked and remembered as a transfer source
    (`provider_transfer_source_at`), so the next snapshot taken after the
    transfer (refresh, `--repair`, a later webhook) applies its emptied
    customer instead of leaving it unresolved.
  - A deleted account (`users.is_active` false, tombstoned uid) is never
    synced. Its kept RevenueCat rows still resolve events for the old uid (a
    store subscription outlives the account), so the legacy handlers run, but
    nothing is read, marked or reported (`webhook.revenuecat.processed`
    `result=account_deleted`); a `TRANSFER` party that is deleted is skipped.
  - `GET /v1/subscribers` is "get or create": an unseen id answers 200 with an
    empty customer, which is treated as "no purchases". It never expires a
    row that could still grant access, except for a transfer source (during
    the `TRANSFER`, or remembered from one whose read failed).
- Web redemption retains its existing identity binding and idempotency
  checks. Finalize applies the verified customer it read with the same
  access sync as webhooks and refresh (`source=web_funnel`): every
  `provider='revenuecat'` row of the user is stamped, the bound product's
  row carries the facts, markers the snapshot answers are cleared, and a
  read older than the stored facts changes nothing. A snapshot the sync
  refuses fails the finalize without writing anything.
- Canonical handoff (`/v1/web-funnel/*`):
  - **Correlation** after anonymous web pay: lead + RC `app_user_id` + redeem
    link digest (never raw URL).
  - **Silent login** (`POST /redemptions/session`, unauthenticated): hash →
    time-limited Firebase custom token for the lead email owner. Repeated
    requests can mint a replacement while the paid redemption is unfinalized
    and nonterminal, so a lost response can be retried. IP-only SlowAPI key;
    issuance time and generation are recorded. Does not bind `preflight_uid`.
    The RevenueCat web-purchase flow is always enabled; custom JWT without
    `wf_silent_login` is refused at preflight/finalize.
  - **Eligibility** (`POST /redemptions/preflight`): verified Firebase email must
    match lead; bind UID to that row before redeem-once. The `custom` provider
    must carry `wf_silent_login`.
  - **Finalize** (`POST /redemptions/finalize`): atomic MealTrack grant;
    idempotent; must select the exact row (hash + preflight UID), not “latest”
    alias match. Opaque preflight receipts / lease-CAS are deferred.
  - `WEB_FUNNEL_LEGACY_CLAIM_ENABLED` gates only the separate compatibility
    flow; when false, lead-UUID
    webhook reconcile does not enqueue `claim_email`, and claim routes/dispatch
    stay off).
- Contracts: `api-endpoints.md`. Billing ownership is RevenueCat Web.
- Paid AI routes are enforced on the server only while
  `SUBSCRIPTION_ENFORCEMENT_ENABLED` and `SUBSCRIPTION_ACCESS_ENABLED` are both
  on (default off); otherwise the mobile app's gates are the only ones. The
  Ghi nhanh describe routes are the exception: their Premium check is always
  on (`docs/api-endpoints.md`).

### Subscription providers (`subscriptions.provider`)

The access policy evaluates every row of a user together, whatever its
provider: the grant with the latest deadline wins (a lifetime grant beats
any), and a row it cannot verify makes the decision "unresolved" only when
nothing else grants. Each row has exactly one owner:

| `provider` | Written by | Identity |
|---|---|---|
| `revenuecat` | RevenueCat snapshots only: webhooks, `POST /v1/me/entitlements/refresh`, the reconcile script, web-funnel finalize | `revenuecat_subscriber_id` = Firebase uid |
| `momo` | The MoMo web checkout and its IPN handler (separate change) | `revenuecat_subscriber_id` NULL |

A writer for any provider other than RevenueCat must:

- set `provider` explicitly (`'momo'` for MoMo; an ORM insert without one
  raises), leave `revenuecat_subscriber_id` NULL, and use `platform='web'`;
- set `provider_snapshot_at` to when the provider confirmed the payment (the
  verified IPN), or the policy treats a granting row as unverified;
- set `expires_at` to the end of the paid period (a fixed-term pass, so
  `will_renew` reads `false`), with `status='active'`, and move the row to
  `expired` or `refunded` itself; leave `provider_sync_required_at` NULL;
- write in one transaction under `lock_subscription_owner` (`SELECT ... FOR
  UPDATE` on the user row), like the RevenueCat writers;
- run the post-purchase side effects itself. The access row only grants
  access; everything else a purchase sets off runs today only from the
  RevenueCat webhook handlers (`src/api/routes/v1/webhook_subscription_lifecycle.py`):
  the referral credit on a first purchase (`credit_referral_on_purchase`),
  the `subscription_*` lifecycle events published to the integration queue
  (affiliate commission and lifecycle messaging consume them), and the
  purge of stale trial-expiry pushes on a renewal. A MoMo payment, renewal
  or refund that only writes its row credits no referrer and tells no
  consumer.

RevenueCat handlers never read, expire, re-point or take over such rows:
webhooks, transfers, refresh, reconciliation and web finalize all filter on
`provider='revenuecat'`, and an empty RevenueCat customer never expires a
row from another provider.

### Refunds and referral commission

Refund clawback is **disabled pending a product decision** (owner: product
with the backend lead). A refund `CANCELLATION` claws back no referral
commission and publishes `subscription_canceled`, exactly as production
did before the subscription-access change; only its access sync is labelled
a refund. `handle_refund` and `revoke_referral_on_refund`
(`webhook_subscription_lifecycle.py`, `webhook_referral_funnel.py`) exist but
nothing dispatches them. The unwired clawback must not be turned on as it
stands:

- It revokes the referred user's one commission whatever transaction was
  refunded. RevenueCat sends a refund `CANCELLATION` for the latest period
  only, so after one renewal every refund is a renewal's ("forgot to
  cancel"), never the purchase that earned the commission, and nothing ever
  restores it (a later purchase does not credit again).
- `revoke_from_wallet` floors the balance at zero but adds the full amount
  to `total_revoked`. A payout request debits the wallet when it is made and
  stays `pending` or `processing` until paid, so a refund after the request
  recovers nothing, leaves no record of the debt and does not flag the
  payout. `RequestPayoutCommandHandler` also reads the wallet without
  `FOR UPDATE`, so a concurrent revoke can be overwritten.
- `subscription_refund` was never emitted in production. Before any refund
  publishes it instead of `subscription_canceled`, confirm that
  nutreeai_async and nutree-affiliate handle it idempotently, matched to the
  refunded transaction, and write the accepted `lifecycle_type` values down
  as a shared contract.

A correct design needs at least:

- revocation tied to the credited transaction: store the transaction that
  earned the commission on `referral_conversions` (the paid
  `INITIAL_PURCHASE`, or the trial-converting `RENEWAL`) and revoke only on a
  refund of that transaction;
- `REFUND_REVERSED` handling that restores a conversion the same transaction
  revoked;
- payout interaction: an outstanding clawback (or a negative balance) that
  later credits pay down, `total_revoked` counting only what was recovered,
  pending or processing payout requests flagged (or reduced, or blocked from
  approval), and the wallet locked in the payout handler;
- the lifecycle consumer contract above.

### Store policy (MoMo channel)

MoMo is a **web checkout channel**, not an in-app payment method. In the
Vietnam storefronts the iOS and Android apps keep selling subscriptions
through App Store and Google Play billing (via RevenueCat) and must not show
MoMo buttons, links, prices or other calls to action that point to buying
elsewhere (App Store Review Guidelines 3.1.1 and 3.1.3; Google Play Payments
policy). A buyer pays with MoMo on the web while signed in with the same
Firebase account, and the app unlocks access by signing in with that account
(Apple guideline 3.1.3(b), multiplatform services). In-app wording stays
neutral ("Already subscribed? Sign in"), and the same subscription must stay
on sale as in-app purchase in both apps. Have legal confirm this before the
MoMo channel launches, and re-check Google's alternative-billing programs
for Vietnam before changing anything on Android.

### Sentry ownership

- Runtime code uses the `src.observability` facade only. Direct `sentry_sdk`
  imports belong in `src/infra/monitoring/sentry.py`.
- One root-cause ERROR per unexpected request failure (API exception handlers).
  Do not log-and-rethrow (duplicate Sentry issues). Background/cron boundaries
  own their own capture when they swallow exceptions.
- Expected 4xx domain failures are silent at ERROR level.
- Severity contract: INFO = normal; WARNING = degrade/retry/slow; ERROR =
  user-impacting; CRITICAL = service unusable. Same privacy allowlist as above.
- Use Sentry metrics for operational AI failure alerts.

### nutree-affiliate boundary

MealTrack and nutree-affiliate are **separate services and databases**. Never
join or write affiliate tables from MealTrack.

- Sync path: HMAC-signed HTTP via `affiliate_service_adapter` +
  `affiliate_event_outbox` + cron dispatcher.
- Identity: `mealtrack_user_id` here; `affiliate_id` only in affiliate service.
- Architecture ownership table: `system-architecture.md` → Affiliate System
  Boundary.
- Signing contract and outbox retry live in the adapter, model, and
  `tests/unit/infra/adapters/test_affiliate_service_adapter_signing.py`.

### Health surfaces

Discover paths in OpenAPI. Typical ops probes: `/health`, `/v1/health/db-pool`,
`/v1/health/db-connections`, `/v1/health/notifications`.

---

## Related

- Architecture: `system-architecture.md`
- Database: `database-guide.md`
- HTTP conventions: `api-endpoints.md`
- Provider outage: `runbooks/provider-outage.md`
