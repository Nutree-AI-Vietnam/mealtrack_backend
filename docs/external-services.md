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
| RevenueCat | **Billing sync** | Webhook/cache degrade; last-known subscription where available. Premium route gates are not enforced yet |
| Redis optional caches | **Optional** | Bypass cache; continue from source of truth |
| Redis provider budget | **Required for provider-origin manual writes** | Fail closed with `NUTRITION_PROVIDER_UNAVAILABLE` |
| Redis meal-suggestion sessions | **Required transient state** (current design) | Session writes fail when store unavailable |
| In-process integration events | **Best-effort follow-up** | Background tasks in the API process after commit; a failing handler is logged and skipped, work running at shutdown is dropped after the 5 s drain |
| OpenAI translation | **Optional read-path localization** | Return canonical local/provider results without translated names; do not block startup or writes |
| OpenAI, FatSecret, USDA, OFF, Brave, image stock APIs | **Optional enrichment** | Degrade to local/prior results when safe |
| Sentry | **Optional observability** | Local logs only; facade no-ops without DSN |
| Google Play Developer API | **Optional** (renewal cancel) | Log ERROR (Sentry); the webhook still answers 200 |
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

**Integration event rule:** follow-up work for a committed write (meal
insights, Firebase account cleanup, profile cache purges) runs on a background
task in the API process after the response. It is best-effort: a handler
failure is logged, and work still running at shutdown is dropped after the 5 s
drain. Never rely on it for read-after-write freshness; the write path purges
its own caches before responding.

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
| Integration events (in-process) | `src/app/events/in_process_event_router.py`; handlers in `src/api/services/in_process_integration_events.py`; publisher in `src/bootstrap/integration_services.py` |
| Stock / generated images | `pexels_image_adapter.py`, `unsplash_image_adapter.py`, `imagen_image_generator.py`, `pollinations_image_generator.py`, `cloudflare_workers_image_generator.py` |
| RevenueCat | `src/infra/adapters/revenuecat_adapter.py`; webhook entry in `src/api/routes/v1/webhooks.py` with sibling modules `webhook_subscription_lifecycle.py`, `webhook_lifetime_purchase.py`, `webhook_subscription_rows.py`, `webhook_lifecycle_events.py`, `webhook_referral_funnel.py`, `webhook_lookup_parsing.py` |
| Google Play Developer API | `src/infra/adapters/google_play_subscription_adapter.py`, `google_play_auth.py`; webhook side `webhook_play_renewal_cancel.py` |
| Web funnel redemption | `src/infra/services/web_funnel_*` |
| Resend email | `src/infra/adapters/resend_email_adapter.py` |
| Redis | `src/infra/cache/` |
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

### Integration events

- Handlers publish one versioned `IntegrationEvent` after commit;
  `BestEffortIntegrationEventPublisher` runs it on a background task and
  `InProcessIntegrationEventRouter` runs every handler registered for its type.
  Handlers are registered at startup by
  `src/api/services/in_process_integration_events.py`; a failing handler does
  not skip the others.
- Meal events generate meal value insights, fenced by the event's
  `occurred_at`: an older generation never overwrites a newer one, and edits
  or deletes clear the old insight. Overdue insights are re-requested from the
  status read (`meal_insight_recovery`).
- Account deletion events delete the Firebase account with three attempts,
  then log the UID at ERROR for `scripts/delete_missed_firebase_accounts.py`.
- Profile, cheat-day and saved-suggestion events purge their caches. Their
  pattern deletes scan 1,000 keys per round trip and stay off the write path.
  Meal, hydration and movement day caches are purged on the write path, so
  those events need nothing more.
- These handlers replaced the Cloudflare Queue and the `nutreeai_async`
  worker:
  [`decisions/261105-0248-retire-nutreeai-async.md`](decisions/261105-0248-retire-nutreeai-async.md).
  Web-funnel claim and reconciliation jobs keep their separate transactional
  outbox.

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

- Webhooks update local `subscriptions`; signature verification is constant-time.
- Canonical handoff (`/v1/web-funnel/*`):
  - **Correlation** after anonymous web pay: lead + RC `app_user_id` + redeem
    link digest (never raw URL).
  - **Silent login** (`POST /redemptions/session`, unauthenticated): hash →
    Firebase custom token (the session it starts does not expire) for the
    account this lead creates under its reserved UID. An email that already
    has a Nutree user or Firebase record gets a 404 and must sign in to that
    account instead. Repeated
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
- Premium feature gates are planned, not enforced on routes.

### Lifetime purchases

- `nutree_lifetime_1299k` and the yearly-to-lifetime upgrade
  `nutree_lifetime_upgrade_700k` (`src/domain/constants/lifetime_products.py`;
  a Play `:suffix` is dropped, rows store the base id) arrive as
  `NON_RENEWING_PURCHASE`. The upgrade converts the running yearly row; a
  lifetime bought outright gets its own row. Either is `active` with a NULL
  `expires_at`, and a `transaction_id` already stored is a no-op. A sandbox
  purchase never converts a production row.
- A subscriber can hold several rows (web-funnel claims, transfers), so
  handlers pick rows in `webhook_subscription_rows.py`, and every handler
  takes a per-subscriber advisory lock before reading them.
  `has_entitled_subscription` counts a NULL expiry only for lifetime products.
- While a lifetime row grants access, a renewing-product event changes only
  its own product's row: no other row, no new row, no payment-failed push. A
  refunded lifetime row is history: the next plan gets its own row.
- One store account can hold rows for two Nutree accounts. The lifetime paths
  (the upgrade, plan events beside lifetime, the restore) prefer the event
  user's row, and the row they update moves to the event user.
- RevenueCat has no REFUND event: a `CANCELLATION` of a lifetime product is
  its refund and marks only that purchase's row (by transaction, else product
  in the same environment). RevenueCat is asked before the unit of work
  opens. If no other row grants access, the renewing plan it still reports is
  written to a plan row of the same environment and platform, or a new one.
  An upgrade refund RevenueCat cannot answer is logged at ERROR and returns
  503 before any write, so it is redelivered.
- The referral credit is revoked only when a refund leaves no paid access.
  Only a lifetime bought outright by someone with no earlier paid row is
  reported as `subscription_initial_purchase`; the upgrade never is, and the
  yearly purchase stays the initial one even when its webhook lands second.
- A transfer moves the old subscriber's rows of the event's store and relinks
  them to the new user. Only stores with a certain platform filter rows
  (`store_platform`: App Store, Play Store, and RC_BILLING, Paddle and Stripe
  for web); any other store moves every row. `parse_platform`, which records
  new rows, is unchanged.

### Play renewal cancel on lifetime

- **Scope.** Every renewing Play plan of a Play lifetime holder is stopped,
  weekly and monthly as well as yearly. That is intended: one `standard`
  entitlement covers all of them, so a plan beside lifetime only charges again.
- **Triggers.** A lifetime purchase or upgrade from `PLAY_STORE` picks the
  event user's Play plan rows of the same environment that aren't lifetime or
  refunded and whose expiry is unknown, ahead or under 90 days old, read
  before the upgrade converts the yearly row. A Play `INITIAL_PURCHASE` or
  `RENEWAL` while that user holds a Play lifetime row that grants access
  names its own plan by order id (the upgrade's webhook landed first). A
  redelivered lifetime purchase picks nothing.
- **Checks, after the unit of work commits, inline within 10 s.** RevenueCat
  must list the plan's order (compared without the `..N` renewal suffix)
  under the event's subscriber, else nothing is cancelled. `orders.get` turns
  the order id into the purchase token; `purchases.subscriptionsv2.get` must
  show the product's line item with auto-renew on and the state ACTIVE,
  IN_GRACE_PERIOD, ON_HOLD or PAUSED, so a plan in billing retry is stopped
  and one already off is left alone. Then `purchases.subscriptionsv2.cancel`
  (`DEVELOPER_REQUESTED_STOP_PAYMENTS`) stops the next payment; access lasts
  to the end of the paid period. A timeout, network failure or 5xx is tried
  once more; a 4xx isn't.
- **Failures** are logged at ERROR (Sentry) with our user id and each plan's
  order id and product, never a purchase token or a traceback, and the
  webhook still answers 200. So is every skipped cancel while the key or
  package is unusable. App Store and web events never call Google.
- **Settings.** Always on, no switch. `GOOGLE_PLAY_PACKAGE_NAME` (default
  `com.nutreeai.mobile`; set `com.nutreeai.mobile.staging` on staging),
  `GOOGLE_PLAY_CANCEL_SANDBOX`
  (default `false`; sandbox events are skipped unless on),
  `GOOGLE_PLAY_API_TIMEOUT_SECONDS` (default 3). Calls are signed in as the
  backend's Firebase service account (`FIREBASE_CREDENTIALS` file, else
  `FIREBASE_SERVICE_ACCOUNT_JSON`), scope `androidpublisher`.
- **Setup per environment, per app only:**
  1. In that backend's Firebase project's Google Cloud console, enable the
     Google Play Android Developer API.
  2. In Play Console, Users and permissions, invite that service account
     (`client_email` of the key) with app permissions on that environment's
     app only: the staging account on `com.nutreeai.mobile.staging`, the
     production account on `com.nutreeai.mobile`. Grant "View financial data"
     and "Manage orders and subscriptions". Never grant account-level
     permissions. A 401/403 right after the invite can be Play still applying it.
- **QA on staging:** set `GOOGLE_PLAY_CANCEL_SANDBOX=true`, buy the yearly then
  the upgrade on the Play internal build with a license tester, and check for
  "Play renewal cancelled" in the logs and "Canceled" in Play's subscriptions.
- **Runbook, a failed cancel.** In Sentry, search the backend project for
  "Play renewal" at level error ("cancel failed", "ran out of time", "not
  cancelled", "on but unusable"). The message holds `plans=<order id>|<product>`
  and our `user_id`. If the order is listed "under another subscriber",
  check the account in RevenueCat before acting. Otherwise, in Play Console
  open Order management, search the order id (without any `..N`), open the
  order and choose to cancel the subscription so it doesn't renew; don't
  refund. Afterwards RevenueCat sends a CANCELLATION for the plan.

### Payment-failed push

The only engagement notification the server sends; the app plans the rest
(`plans/261026-notification-catalog/README.md`).

- `ENGAGEMENT_PUSH_ENABLED` (default `false`) gates both halves: the webhook
  queues nothing while it is off, so turning it on never sends a backlog at
  night. With it on, every API process runs the dispatcher; claims use
  `FOR UPDATE SKIP LOCKED`, so processes share the queue safely.
- `BILLING_ISSUE` queues one `billing_issue` row in the `notifications`
  outbox, due now if it is 09:00–20:00 in `users.timezone`, else at the next
  09:00 there, and expiring 48 h later. A repeat `BILLING_ISSUE` while the
  subscription is already `billing_issue` queues nothing (one push per
  episode); the outbox also keeps at most one row per user, kind and local
  date.
- `RENEWAL`, `INITIAL_PURCHASE` and `PRODUCT_CHANGE` delete the user's pending
  `billing_issue` row. Outbox writes run in a savepoint and never fail the
  webhook.
- The dispatcher polls every 30 s, 20 rows at a time. A claimed row is
  committed as `processing` before the push goes out (a crash can lose a
  push, never repeat it), then ends `sent` or `failed` (no device, or no
  device accepted it); rows past `expires_at` become `expired`. Copy is in
  `src/domain/model/notification/engagement_push_copy.py`; FCM data is
  `{"type": "engagement", "kind": "billing_issue", "route": "/profile"}`
  with collapse key `billing_issue`.
- Not built yet: retrying a temporary FCM failure, and deleting old outbox
  rows.

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
