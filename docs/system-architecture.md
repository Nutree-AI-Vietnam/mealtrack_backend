# Backend System Architecture Overview

**Status:** Evergreen architecture authority  
**Architecture:** 4-Layer Clean + CQRS + Event-Driven  
**Event Bus:** PyMediator (singleton registry pattern)  
**Inventory:** discover live layout under `src/` (`api/`, `app/`, `domain/`,
`infra/`, plus root/bootstrap/cron); do not hand-maintain file or LOC counts here

---

## Onboarding calculation and result boundaries

The unauthenticated TDEE preview is a bounded API boundary: request bodies are
limited to 8 KiB before parsing and protected by an IP quota. Its versioned
`onboarding_preview_v2` contract is authoritative for motivation/activity
onboarding; canonical no-training is `(0, 0)`, while `(0, 15)` is a legacy
compatibility shape. Keto applies 5/20/75 and calories are derived from the
returned macro triple. Complete custom triples and target/cache revision fences
prevent partial edits and stale responses. Body-fat projections are
illustrative, source-guarded UI data rather than clinical measurements. The
supporting migration exists but is not applied or deployed; external release,
device, and purchase gates remain open.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         API Layer                            │
│  HTTP Routing │ Pydantic Validation │ Auth │ Middleware      │
└────────────────────────┬────────────────────────────────────┘
                         │ Commands/Queries
┌────────────────────────▼────────────────────────────────────┐
│                    Application Layer                         │
│  CQRS Handlers │ Event Publishing │ App Services             │
└────────────────────────┬────────────────────────────────────┘
                         │ Domain Services
┌────────────────────────▼────────────────────────────────────┐
│                      Domain Layer                            │
│  Business Logic │ Domain Models │ Port Interfaces            │
└────────────────────────┬────────────────────────────────────┘
                         │ Port Implementations
┌────────────────────────▼────────────────────────────────────┐
│                 Infrastructure Layer                         │
│  DB │ Cache │ External APIs │ Event Bus │ Config             │
└─────────────────────────────────────────────────────────────┘
```

**Layer rule:** Domain has no outer-layer or external I/O dependencies. See `cqrs-guide.md` for handler patterns.

---

## Key Architectural Patterns

### Dependency Inversion
Domain defines port interfaces; infrastructure implements them. Handlers depend on abstractions.

### CQRS
- **Commands** — write operations, publish events (CreateManualMeal, UpdateUserMetrics)
- **Queries** — read-only, return immediately (GetMealById, SearchFoods)
- **Events** — fire-and-forget, processed async (MealCreated, UserOnboarded)

### Event Bus (PyMediator Singleton)
```python
result = await event_bus.send(CreateManualMealCommand(...))    # synchronous
await event_bus.publish(MealCreatedEvent(...))           # fire-and-forget
```

Background subscriber tasks are owned by `BackgroundTaskManager` (`src/infra/event_bus/background_task_manager.py`), which replaces bare `asyncio.create_task` in the event bus and routes; it exposes `spawn`, `drain`, and `shutdown` so subscriber failures are observable and app shutdown can cancel outstanding tasks cleanly.

### Repository Pattern
Async SQLAlchemy repositories are accessed through `AsyncUnitOfWork`. The UoW owns commit/rollback boundaries; repositories flush only when generated IDs or relationship state are needed.

### Meal Recommendation Ranking
Catalog-backed meal recommendations use snapshot-scoped ingredient IDF, confidence-scaled ingredient similarity, and bounded top-30 diversity reranking for new plan generation, without changing endpoint paths. The current scoring weights use `ingredient_weight = 0.35 * confidence` and `diversity_weight = 0.10`, with calorie weight filling the remainder.

The active catalog snapshot owns both immutable meal projections and snapshot-scoped ingredient IDF statistics. Persisted plans replay their stored candidates and scores instead of recalculating.

Phase 0 persistence is four-table only: `meal_catalog`,
`meal_catalog_ingredients`, `meal_recommendations`, and
`meal_recommendation_operations`. The AI `/v1/meal-suggestions` flow remains a
separate additive system and is not a fallback or replacement for these
catalog-backed recommendations.

### Public Catalog Browser

The authenticated browse route (`GET /v1/meal-catalog` and
`GET /v1/meal-catalog/{catalog_id}`) reads the same immutable catalog snapshot
as deterministic recommendation generation. Browse requests do not create plan
rows, candidate rows, or meal logs.

`popular` is a curated view ordered by the nullable `meal_catalog.popularity_rank`
with stable name/ID tie-breakers. The route fails closed with 503 until that
curated signal has been seeded instead of fabricating popularity from UUID or
timestamp order.

`for_you` reuses the shared snapshot, current daily target, linked 90-day
ingredient affinity, and deterministic scoring. When the user has no usable
history or the target cannot be resolved, the route falls back to the curated
global order and reports `fallback=true` with `ranking_source=curated`; a warm
personalized result reports `ranking_source=personalized`. `allergy_evaluated`
remains false until canonical allergen evaluation is implemented.

### Local-First Food Search
Manual food search reads Redis cache when available, then searches verified
`food_reference` rows locally before provider fill. Cache, provider, and
translation failures degrade to bounded local results when possible. Local
result calories are always derived from stored macros using the backend formula.

### Translation boundary

Read-path localization is presentation-only. The application layer owns the
translation service used by catalog and suggestion responses; the infrastructure
adapter uses the OpenAI Responses API with payload storage disabled. Persisted
meal translation rows are versioned against the active translation contract, so
rows written before that version are invalidated and retranslated on demand.
The domain translation service returns explicit outcomes, and only
`TranslationOutcome.TRANSLATED` may be persisted or admitted to locale caches.
`PARTIAL`, `PASSTHROUGH`, and `UNAVAILABLE` keep canonical text in the response
path.

Fresh meal-image analysis uses a same-call bilingual structured response. The
vision contract keeps English dish and food identities as the canonical source
for nutrition and reference validation, while requested-language display names
are validated and persisted on the `Meal` and `FoodItem` display fields. Image
meals are identified by the existing `source="scanner"` marker and return
those stored names on later reads regardless of the request locale. The stored
raw analysis payload retains the English canonical fields for validation,
nutrition, and canonical identifiers. Fresh image analysis does not create a
`MealTranslation` row or reload the meal to obtain localized content; meals
with existing translation rows continue through the versioned read-path
fallback.

### Nutrition Integrity Policy
Structured nutrition crosses one versioned domain policy,
`nutrition_integrity_v1`, before it is trusted by parse finalization, search
output, provider mapping, catalog approval, or verified reference upsert. The
policy rejects non-finite or out-of-range macros, impossible macro mass,
out-of-range or mismatched energy, and invalid serving conversions. The
canonical `g` conversion is exactly one gram; a provider's 100g basis is a
labelled serving and never the base `g` unit. Search and editorial trust
boundaries fail closed, while the best-effort reference validator reports a
failure and returns the original nutrition unchanged. Logical source identity
is normalized separately from semantic preparation matching. Search and parse
responses expose one tagged origin plus namespaced opaque provider identity,
with `food_reference:<id>` retained only as a matching local alias. Provider
identity is persisted as nullable `source_namespace` and `source_food_id`; a
partial unique identity index prevents duplicate source rows while leaving
legacy rows explicitly unknown. Local search filters through this policy before
dedupe and limit, requiring verified references and continuing candidate batches
until the valid-result limit is filled or candidates are exhausted. Provider
serving labels are normalized through the same policy. Versioned cache keys
include the active policy version to prevent stale serving contracts.

### Authoritative manual meal writes
V2 manual create and edit requests carry an explicit nutrition contract, source
identity, and idempotency key. `ManualMealNutritionResolver` resolves local,
USDA, provider, and custom items server-side; client macros, gram weights, and
serving lists do not replace reference nutrition. Each successful v2 item stores
an immutable source snapshot on `food_item`, and meal detail reads use that
snapshot before consulting legacy references. Edit-replace of the same
`food_reference_id` rebuilds the snapshot from current catalog per-100g density;
there is no dedicated refresh HTTP route. GET display names for tracked items
come from live catalog `name` / `name_vi` while macros stay snapshot-derived.
User-scoped write-operation leases
make retries replayable and fence stale workers before a meal mutation commits.
The additive contract is advertised only through
`/v1/capabilities/durable-writes` after the persistence migration is available.
Optional client `meal_id` / item `id` on manual create are INSERT-only PKs
(omit → mint; never upsert). Conflict codes:
`docs/decisions/260826-client-stable-meal-and-item-ids.md`.

### Observability Connector
Observability uses a provider-neutral facade at `src.observability` so API middleware does not import infrastructure directly. Startup composition wires it through `src.bootstrap.observability`. The compatibility export at `src.infra.monitoring` remains for cron and infrastructure services. Direct `sentry_sdk` imports are isolated to `src/infra/monitoring/sentry.py`.

The connector sends unexpected API failures, `ERROR` logs, sampled request/SQL/cron spans, explicit Sentry Logs, operational metrics, swallowed cron failures, and affiliate outbox permanent failures. It does not send expected 4xx/business errors, product analytics, request bodies, auth headers, Firebase claims, emails, food payloads, raw image URLs, provider payloads, or secrets. Context, log attributes, and metric attributes are allowlisted scalar values.

### Exception Ownership by Layer (Single-Owner Logger)

**Rule: log-or-raise, not both.** One root-cause `ERROR` per unexpected request failure.

| Layer / File | Role | Log behavior |
|---|---|---|
| `src/api/exception_handlers.py` | Global FastAPI boundary | Owns single `ERROR` for unexpected exceptions; `MealTrackException` converts silently (no log); `AIUnavailableError` logs `WARNING` |
| `src/api/middleware/request_logger.py` | Outcome indicator | 5xx response lines logged at `WARNING` — never `ERROR`; root-cause ERROR is upstream |
| Command/query handlers | Pure conversion | Call `handle_exception()` or propagate directly; do **not** log before re-raising |
| Event handlers / background tasks | Swallowing boundary | Own their `ERROR` log + `capture_exception` at subscriber boundary |
| `src/cron/email.py`, `src/cron/push.py` | Cron boundary | `capture_exception` + `flush_observability` on failure; `log_event("info", "cron.phase.completed")` per phase |

Architecture guardrails enforced by `tests/unit/architecture/test_logging_ownership_guardrails.py`: bans direct `sentry_sdk` outside connector, log-and-rethrow pattern, and sensitive substrings in log statements.

---

## Bounded Contexts (Domain)

| Context | Key Entities |
|---------|-------------|
| Meal | Meal (state machine), MealImage, Ingredient, image cache projection |
| Nutrition | Nutrition, FoodItem, Macros, Micros, optional meal/item `NutritionOverride` |
| User | User, UserProfile, Activity, TdeeRequest, weight history |
| Hydration | Hydration entries, drink catalog, caloric drink logging |
| Movement | Movement entries, activity catalog, daily movement summaries |
| Progress | Journey progress snapshot, active-period filtering, action scoring |
| Meal Planning | Weekly budget, meal planning, meal suggestion, saved suggestion models |
| Notification | UserFcmToken, NotificationPreferences, PushNotification, queued notification rows |
| AI | GPTAnalysisResponse, GPTFoodItem, GPTResponseError |
| Commerce | Subscription state, RevenueCat web-funnel redemption, referral codes, promo codes |

### Calories and nutrition overrides

Default rule: meal calories are derived from macros with the fiber-aware formula
owned by the backend (AGENTS MUST-Follow). Exception: when a meal-level or
ingredient-level `NutritionOverride` is set, the backend presents the absolute
override values (including calories) until cleared. Source macros remain
available for restore. HTTP shape: `api-endpoints.md`.

### Meal scan vs hydration

Meal image analyze and scan-by-url treat visible edible or drinkable intake as
normal meal nutrition (`Meal`, typically `source="scanner"`). They must **not**
create `hydration_entries`. Caloric drinks are foods; water and zero-cal
hydration drinks are logged only through `/v1/hydration/*`. Food-label scan is a
separate path (`source="food_label"`) with validated label contracts. Non-null
`beverage_metadata` on meal-scan output fails validation. Legacy
`source="scan_beverage"` hydration rows may still exist for compatibility reads;
new scans must not create them.

---

## Data Flow Example: Meal Image Analysis

1. `POST /v1/meals/image/analyze` receives image bytes.
2. Route creates `UploadMealImageImmediatelyCommand`.
3. `EventBus.send()` calls `UploadMealImageImmediatelyHandler`.
4. Handler uploads to Cloudinary, runs `VisionAIService`, parses nutrition, and persists a READY `Meal(source="scanner")` with **no hydration side effects**.
5. If `AI_MEAL_ANALYZE_GRAPH_ENABLED=true`, the handler enters `MealAnalyzeWorkflow`; the app-layer graph owns image acquisition, vision parsing, persistence, cache invalidation, and meal value insight scheduling.
6. If `AI_MEAL_ANALYZE_FATSECRET_VALIDATION_ENABLED=true`, optional reference validation may run after meal creation. Provider timeout or mismatch keeps the original meal result.
7. Meal value insight scheduling is best-effort after persistence and cache invalidation. It stores only safe state fields such as `meal_value_insight_scheduled` and never blocks the READY meal response.
8. Handler returns `DetailedMealResponse` synchronously.

`POST /v1/meals/scan-by-url` follows the same synchronous workflow after
downloading Cloudinary bytes. Graph nodes must not import provider SDKs,
`sentry_sdk`, SQLAlchemy, API-layer services, or domain-internal vendor code.

## Data Flow Example: Food-Label Scan By URL

1. `POST /v1/meals/food-label/scan-by-url` receives Cloudinary image identifiers, optional label-crop identifiers, and crop metadata.
2. Route validates Cloudinary URLs and creates `ScanByUrlCommand(scan_mode="food_label")`.
3. `ScanByUrlCommandHandler` downloads the full image bytes; if a label crop is supplied, it downloads and analyzes the crop bytes.
4. Handler calls `VisionAIService.analyze_with_strategy(..., FoodLabelImageAnalysisStrategy)`.
5. Infrastructure validates provider output against `FoodLabelNutritionResponse`.
6. `VisionResponseParser` maps validated label data into `Nutrition` and `food_label_metadata`.
7. Handler persists a READY `Meal(source="food_label")`, invalidates meal caches, and does not create hydration side effects.

## Data Flow Example: Meal Recommendation Plan

1. `POST /v1/meal-recommendations/three-day` resolves timezone and daily calories, then builds or replays a durable recommendation plan.
2. The plan-level response is compact: it includes only the selected slots, with ingredients for each selected meal. Alternatives, scores, and full meal-detail fields stay out of the summary payload.
3. `GET /v1/meal-recommendations/{plan_id}/slots/{slot_id}` hydrates one selected slot and its alternatives when the client needs drill-down data.
4. `swap`, `log`, and `skip` return the changed-slot detail shape so the mobile client can patch its cached plan without reloading everything.
5. Recommendation analytics are scheduled through `BackgroundTaskManager` when available. Catalog meals are read from the process-local snapshot service with revision-aware TTL, single-flight refresh, and last-good fallback. Meal-history affinity is projected from aggregate linked ingredient buckets instead of loading the full meal graph, and logging a recommended meal reuses the already loaded selected catalog projection without fabricating image data.
6. Candidate rows retain `seen_at` and `retired_at`. Swap locks only the requested slot, consumes unseen active candidates, and when exhausted retires that slot's inactive pool before inserting five deterministic fresh alternatives. Replenishment never mutates another slot or changes the response contract; daily jobs remain out of scope.

---

## Affiliate System Boundary

MealTrack and nutree-affiliate are **separate services with separate databases**. MealTrack must never join against or directly write affiliate tables.

```
Nutree mobile ──→ MealTrack (validate/apply code)
                       │
                       │ same DB transaction
                       ▼
               affiliate_event_outbox
                       │
                       │ cron every 5 min
                       ▼
              nutree-affiliate API  ──→ nutree-affiliate DB
                 (Vercel)              (commission ledger, payout)
```

**Identity separation:** A nutree app user and an affiliate user are distinct identities even when their email addresses match. MealTrack holds only `mealtrack_user_id`; nutree-affiliate holds only `affiliate_id`.

**Ownership rules:**

| Data | Owner |
|------|-------|
| App users, subscriptions, RevenueCat events | MealTrack |
| Affiliate identity, codes, commission rules | nutree-affiliate |
| Ledger credits/reversals, payout state | nutree-affiliate |
| `affiliate_event_outbox` retry queue | MealTrack (infrastructure only) |

**Integration:** See `external-services.md` (nutree-affiliate boundary) and
`src/infra/adapters/affiliate_service_adapter.py`.

---

## Known Issues

- Premium features not restricted on routes (`require_premium` dependency not applied)
- No API versioning strategy beyond v1
- Hardcoded constants (MAX_FILE_SIZE, SLOW_REQUEST_THRESHOLD) not in config
- CORS is configured only when `ALLOWED_ORIGINS` is set; production origin values still need deployment review.
- `AsyncUnitOfWork` uses `asyncio.Lock`; concurrent reuse within one instance will block (by design — use separate instances per handler, enforced by event bus handler cloning)
- Database runtime is async-only: request paths, cron jobs, and handlers use `config_async.py`, `AsyncSession`, `AsyncUnitOfWork`, and async repositories. Alembic uses its separate migration engine.
- Manual meal save (`POST /v1/meals/manual`) instruments `db_ms` and `cache_ms` in structured logs so DB commit and Redis cache invalidation latency are independently observable without logging food payload or auth data.

---

See related: `cqrs-guide.md`, `database-guide.md`, `external-services.md`, `code-standards.md`
