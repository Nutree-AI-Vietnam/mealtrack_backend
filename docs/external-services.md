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
| Cloudinary | **Best-effort media** | Degrade (fallback URL construction where coded) |
| RevenueCat | **Billing sync** | Webhook/cache degrade; last-known subscription where available. Premium route gates are not enforced yet |
| Redis optional caches | **Optional** | Bypass cache; continue from source of truth |
| Redis provider budget | **Required for provider-origin manual writes** | Fail closed with `NUTRITION_PROVIDER_UNAVAILABLE` |
| Redis meal-suggestion sessions | **Required transient state** (current design) | Session writes fail when store unavailable |
| Cloudflare Queue cache invalidation | **Required when `CLOUDFLARE_QUEUE_ENABLED=true`** | Outbox publish retries until the Queue accepts the event |
| Upstash Redis REST for Worker deletes | **Required for the Queue consumer** | Worker retries, then DLQ, on Redis/network/delete failures |
| OpenAI translation | **Optional read-path localization** | Return canonical local/provider results without translated names; do not block startup or writes |
| OpenAI, FatSecret, USDA, OFF, Brave, image stock APIs | **Optional enrichment** | Degrade to local/prior results when safe |
| PostHog | **Optional analytics** | Skip capture |
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
exception. The backend commits the hydration row first, then publishes
`hydration.created.v1` directly to the environment ingress Queue when
`CLOUDFLARE_QUEUE_ENABLED=true`. In local or disabled mode, the hydration
handler skips publication entirely because no Queue publisher is injected. The
Worker orchestrator invokes registered handlers in order and ACKs only after
all handlers succeed. A failure retries the whole ingress message and
eventually reaches the ingress DLQ. There is no HMAC, no local-vs-Cloudflare
dual routing, no percentage canary, no D1 delivery ledger, and no cache-value
write path in this slice. The accepted MVP trade-off is that a post-commit
Queue failure can lose the hydration event; only the committed database row is
durable. Other durable background flows still use the PostgreSQL transactional
outbox.

---

## Adapter map (WHERE)

| Concern | Owner paths |
|---------|-------------|
| Settings / env | `src/infra/config/settings.py` |
| Firebase Auth + FCM | `src/infra/services/firebase_service.py`, `firebase_auth_service.py`, `src/infra/services/push/` |
| Cloudinary images | `src/infra/adapters/cloudinary_image_store.py` |
| AI routing / circuit breaker | `src/infra/services/ai/` (`ai_model_manager.py`, providers, adapters) |
| Vision analysis | `src/infra/adapters/vision_ai_service.py` |
| OpenAI prompt-cache policy | `src/infra/services/ai/openai_prompt_cache_policy.py` |
| Food providers (USDA, FatSecret, OFF, Brave) | `src/infra/adapters/food_data_service.py`, `fat_secret_service.py`, `open_food_facts_service.py`, `brave_search_nutrition_service.py` |
| Translation | `src/infra/adapters/openai_translation_adapter.py` |
| Cloudflare cache invalidation relay | `src/infra/adapters/cloudflare_queue_publisher.py`, `src/infra/services/handlers/cache_invalidation_queue_handler.py` (dispatch trigger removed — see `docs/decisions/ADR-cron-jobs-removal.md`) |
| Stock / generated images | `pexels_image_adapter.py`, `unsplash_image_adapter.py`, `imagen_image_generator.py`, `pollinations_image_generator.py`, `cloudflare_workers_image_generator.py` |
| RevenueCat | `src/infra/adapters/revenuecat_adapter.py`; webhook entry in `src/api/routes/v1/webhooks.py` with sibling modules `webhook_subscription_lifecycle.py`, `webhook_referral_funnel.py`, `webhook_lookup_parsing.py` |
| Web funnel redemption | `src/infra/services/web_funnel_*` |
| PostHog | `src/infra/adapters/posthog_adapter.py` |
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

- Non-hydration mutation paths may publish `cache_invalidation.v1` through the
  transactional outbox. The `CacheInvalidationQueueHandler` only forwards
  validated payloads to Cloudflare Queue; it does not write Redis values.
- Hydration creation publishes `hydration.created.v1` directly to Cloudflare
  Queue after its database transaction commits. The Worker orchestrator owns
  its cache translation and invokes the same delete handler.
- `CloudflareQueuePublisher.from_settings()` reads the single global
  `CLOUDFLARE_QUEUE_ENABLED` switch plus the account, queue name, token, and
  timeout settings. When disabled, local hydration writes skip integration
  publication entirely; when enabled but misconfigured, the request fails
  after the database commit and the publish error is logged by the API. The
  MVP accepts that post-commit failure window for hydration.
- The Worker validates the event envelope and deletes exact keys or bounded
  patterns through Upstash Redis REST. Queue ACK/retry/DLQ semantics own the
  delivery lifecycle.
- `HMAC`, revision tables/fencing, cache writes, local-vs-Cloudflare dual
  routing, and percentage canaries are intentionally out of scope for this
  slice.

### Cloudflare integration event orchestration

- The integration-event redesign uses one versioned `IntegrationEvent` as the
  ingress payload. Hydration publishes `hydration.created.v1` directly after
  commit when Queue delivery is enabled; other integration paths may still use
  the transactional outbox.
- Hydration events carry the hydration aggregate ID rather than a snapshot.
  The hydration cache handler queries Neon for the hydration owner and local log
  date, so adding another event does not require another Worker parser.
- One ingress message owns the retry lifecycle. If a handler fails, earlier
  handlers may run again on retry, so handlers must be idempotent.
- Staging and production use separate ingress queues and matching
  `environment` values. The MVP does not use D1 or a dynamic subscription
  catalog.
- Configure one environment-specific `CLOUDFLARE_QUEUE_NAME` for the Worker
  ingress queue. Generic integration events and compatibility payloads share
  this queue during the migration.
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

- Webhooks update local `subscriptions`; signature verification is constant-time.
- Web checkout handoff is `/v1/web-funnel/*` (hash-only redemption link, Firebase
  passwordless, preflight/finalize). Contracts: `api-endpoints.md`.
- Billing ownership is RevenueCat Web; legacy direct Paddle fulfillment is gone.
- Premium feature gates are planned, not enforced on routes.

### Sentry ownership

- Runtime code uses the `src.observability` facade only. Direct `sentry_sdk`
  imports belong in `src/infra/monitoring/sentry.py`.
- One root-cause ERROR per unexpected request failure (API exception handlers).
  Do not log-and-rethrow (duplicate Sentry issues). Background/cron boundaries
  own their own capture when they swallow exceptions.
- Expected 4xx domain failures are silent at ERROR level.
- Severity contract: INFO = normal; WARNING = degrade/retry/slow; ERROR =
  user-impacting; CRITICAL = service unusable. Same privacy allowlist as above.
- Use Sentry metrics for operational AI failure alerts; use PostHog for product
  / LLM product traces — not P1 outage paging.

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
