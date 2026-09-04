# Backend Database Guide

**Status:** Evergreen data ops and connection policy  
**Engine:** PostgreSQL (Neon) + SQLAlchemy 2.0 async runtime (asyncpg)  
**Migrations:** Alembic via `migrations/versions/` and `migrations/run.py`  
**Models:** discover ORM packages under `src/infra/database/models/` (do not hand-maintain table counts here)  
**Extensions:** `vector` for embeddings and `pg_trgm` for indexed local food search  
**Schema judgment:** [`standards/db-api.md`](./standards/db-api.md)

---

## Standards Gate

Before creating or changing tables, read and follow
[`docs/standards/db-api.md`](./standards/db-api.md).

Non-negotiable database rules:

- OLTP source-of-truth tables follow 3NF unless a documented exception exists.
- User-owned tables use `ForeignKey("users.id")` unless retention/legal rules require otherwise.
- JSON must not be the permanent owner of business entities, user preferences, workflow state, or queryable product data.
- Schema migrations use expand-migrate-contract for existing data: add normalized structure, backfill, dual-write/read fallback, cut over, then remove legacy columns later.
- New ORM models must be imported by the central model registry so Alembic sees them.

---

## DB Connection Policy

The app supports two runtime modes controlled by `DB_CONNECTION_MODE`:

| Mode | Pool class | When to use |
|------|-----------|-------------|
| `direct_pool` | `AsyncAdaptedQueuePool` | Default. Use with a direct Neon endpoint (`ep-xxx.region.aws.neon.tech`). App owns the connection pool. |
| `neon_pooler` | `NullPool` | Use with a Neon `-pooler` endpoint. PgBouncer (transaction mode) manages connections. Prepared statement caching is disabled automatically. |

### URL priority

App runtime URL resolution (highest to lowest priority):
1. `APP_DATABASE_URL` — the preferred variable for production
2. `DATABASE_URL` — backward-compat fallback (legacy deploys)
3. Component vars (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`) — local dev only

`DATABASE_URL_DIRECT` is **not** used for app runtime. It is reserved for Alembic migration tooling only.

### Pool capacity formula (direct_pool)

```
total_connections = UVICORN_WORKERS × (ASYNC_POOL_SIZE_PER_WORKER + ASYNC_POOL_MAX_OVERFLOW)
```

`ASYNC_POOL_SIZE_PER_WORKER` maps directly to SQLAlchemy `pool_size` inside each worker process; `ASYNC_POOL_MAX_OVERFLOW` is also per worker. Keep the total below your Neon project's `max_connections` limit. Monitor via `GET /v1/health/db-pool`.

### Pooler mode notes

- Use only when direct connections would exhaust `max_connections` at scale.
- Set `APP_DATABASE_URL` to the `-pooler` endpoint and `DB_CONNECTION_MODE=neon_pooler`.
- PgBouncer runs in transaction mode — prepared statements are disabled automatically.
- `ASYNC_POOL_SIZE_PER_WORKER` and related settings are ignored in pooler mode.
- **Cutover runbook:** [`runbooks/neon-pooler-cutover.md`](./runbooks/neon-pooler-cutover.md) (Stage 2).
- After cutover, `/v1/health/db-pool` reports `pool_type=NullPool` plus `worker_count` (no SQLAlchemy checkout gauges).

### Scale-up order (do not skip Stage 1)

1. **Stage 1 (app bugs):** no nested UoW checkouts; no shared singleton `AsyncUnitOfWork` in `event_bus`; cache-first on hot reads. Required before adding capacity — more workers make nested deadlocks worse.
2. **Stage 2 (500–2k):** Neon `-pooler` + `DB_CONNECTION_MODE=neon_pooler`; raise workers; monitor `/v1/health/db-pool`.
3. **Stage 3 (2k–10k+):** Render autoscaling + Redis read shield for app-open paths; queue non-critical writes.

See `plans/260904-1547-stage1-pool-deadlock-fixes/` and `docs/troubleshooting.md` (QueuePool / nested UoW).

---

## Connection Configuration

```python
# src/infra/database/config_async.py
DATABASE_URL = "postgresql+asyncpg://user:pass@host/db"

async_engine = create_async_engine(
    DATABASE_URL,
    pool_size=3,        # Default per worker
    max_overflow=2,     # Default per worker
    pool_recycle=120,   # Default recycle interval
    pool_pre_ping=True, # Verify before use
)

AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)
```

Runtime code uses `get_async_db` and `AsyncUnitOfWork` for request and handler
transaction boundaries. Alembic uses its migration-specific engine utilities
instead of the request/runtime session factory.

---

## Core Tables

| Table | Purpose | Key Fields |
|-------|---------|-----------|
| **users** | Auth & identity | id, firebase_uid, email, language_code, is_active |
| **user_profiles** | Health metrics | user_id, age, gender, height, weight, body_fat_percentage, date_of_birth |
| **user_profile_preferences** | Normalized profile arrays | profile_id, preference_type, value, position |
| **subscriptions** | RevenueCat cache | user_id, product_id, platform, status, expires_at |
| **meal** | Meal records | meal_id, user_id, status (state machine), dish_name, ready_at |
| **mealimage** | Cloudinary refs | image_id, url, format, size_bytes, width, height |
| **nutrition** | Meal macro facts | meal_id, protein, carbs, fat, fiber, sugar, confidence_score |
| **food_item** | Ingredients | nutrition_id, name, quantity, unit, fdc_id, food_reference_id, is_custom, fiber, sugar |
| **food_reference** | Barcode/food data | barcode, name, name_normalized, nutrition data |
| **food_reference_serving_sizes** | Normalized serving conversions | food_reference_id, name, grams, milliliters, is_default, position |
| **food_reference_nutrients** | Normalized extended nutrients | food_reference_id, nutrient_key, amount, unit |
| **meal_catalog** | Curated catalog meals for recommendations | catalog_key, cuisine, meal_types, content_hash, is_active, image_url |
| **meal_catalog_ingredients** | Catalog ingredients linked to canonical foods | catalog_meal_id, food_reference_id, display_name, quantity, unit |
| **meal_recommendations** | Durable selected and alternative recommendation candidates | batch_id, slot_id, catalog_meal_id, score, selection_version, shown/skipped/logged state |
| **meal_recommendation_operations** | Idempotent recommendation mutation replay | request_id, operation_type, request_fingerprint, result fields |
| **hydration_entries** | Normalized hydration logs | user_id, drink_id, volume_ml, credited_ml, macro facts, logged_at, legacy_meal_id |
| **meal_instruction_steps** | Normalized recipe steps | meal_id, instruction, duration_minutes, position |
| **user_fcm_tokens** | Push tokens | user_id, fcm_token, device_type, is_active |
| **notification_preferences** | FCM settings | user_id, reminder flags, timing, language, is_deleted |
| **notifications** | Prebuilt notification queue | user_id, notification_type, scheduled_date, scheduled_for_utc, context snapshot |
| **saved_suggestions** | Saved meal suggestion headers | user_id, suggestion_id, meal_type, suggestion_data compatibility snapshot |
| **saved_suggestion_items** | Saved suggestion ingredients | saved_suggestion_id, name, quantity, unit, macro facts, position |
| **saved_suggestion_steps** | Saved suggestion recipe steps | saved_suggestion_id, instruction, duration_minutes, position |
| **weekly_macro_budgets** | Weekly target/consumed facts | user_id, week_start_date, target macros, consumed macros |
| **cheat_days** | User cheat day markers | user_id, date, marked_at |
| **weight_entries** | Weight history | user_id, weight_kg, recorded_at |
| **movement_entries** | Movement logs | user_id, activity_name, duration_min, kcal_burned, logged_at |
| **referral_codes** | Referral code ownership | user_id, code, created_at |
| **referral_conversions** | Referral conversion audit | referrer_user_id, referred_user_id, status, commission fields |
| **referral_wallets** | Referral wallet totals | user_id, balance, total_earned, total_withdrawn |
| **payout_requests** | Referral payout workflow | user_id, amount, payment_method, typed masked destination, payment_details snapshot, status |
| **affiliate_event_outbox** | Cross-service affiliate lifecycle dispatch | event_id, event_type, status, attempts, next_attempt_at |
| **ai_handshake_guest_trial_quotas** | Guest trial quota state | install_hash, used_at, expires_at |
| **promo_codes** / **promo_code_redemptions** | Promotional entitlement tracking | code, source_offering_id, redemption state |

---

## Model Mixins

Three base mixins in `src/infra/database/models/base.py`:

| Mixin | ID Type | Use Case |
|-------|---------|---------|
| `PrimaryEntityMixin` | GUID String(36) | Top-level aggregates (Meal, User) |
| `SecondaryEntityMixin` | Auto-increment int | Child entities (Nutrition, FoodItem) |
| `TimestampMixin` | No ID | Join tables or log tables |

All mixins add `created_at` and `updated_at` timestamps.

---

## Relationships

```
User (1:N) UserProfile, Subscription, Meal, NotificationPreferences, UserFcmToken, HydrationEntry
UserProfile (1:N) UserProfilePreference
Meal (1:1) MealImage, Nutrition
Meal (1:N) MealInstructionStep
Nutrition (1:N) FoodItem
SavedSuggestion (1:N) SavedSuggestionItem, SavedSuggestionStep
FoodReference (1:N) FoodReferenceServingSize, FoodReferenceNutrient
MealCatalog (1:N) MealCatalogIngredient
MealCatalog (1:N) MealRecommendation selected/alternative candidates
```

---

## Meal Status State Machine

```
PROCESSING → ANALYZING → ENRICHING → READY
                                    ↓
                                  FAILED
                                    ↓
                                 INACTIVE
```

---

## Migrations

```bash
alembic upgrade head                              # Apply latest
alembic revision --autogenerate -m "description"  # Create new
alembic downgrade -1                              # Rollback one
```

Migration files live under `migrations/versions/`. `alembic upgrade head` is the
standard versioned upgrade path. `python migrations/run.py` is the guarded fresh
bootstrap / recovery path: it upgrades empty databases from base to head and
refuses to stamp existing unversioned application tables automatically. Use
timestamp naming for new migrations and keep app runtime URLs separate from
migration/admin URLs.

**Recent migrations:**

| Version | Changes |
|---------|---------|
| 20260727000001 | Add meal-recommendation candidate lifecycle states |
| 20260726000001 | Relax meal recommendation anchor metadata |
| 20260724000001 | Add meal recommendation skip state |
| 20260723000001 | Add `pg_trgm`, unique normalized food-reference index, and trigram search index |
| 20260716000001 | Add four-table meal catalog recommendation foundation |
| 20260624000001 | Add journey_progress_seed_percent for existing-user journey progress seeding |
| 20260620000001 | Add source_offering_id to promo codes |
| 20260619000001 | Add ai_handshake_guest_trial_quotas for one-shot guest quota state |
| 20260616000001 | Add beverage-specific columns to hydration entries |
| 20260610000001 | Add affiliate_event_outbox for cross-service affiliate lifecycle dispatch |
| 20260609000006 | Add normalized read-path indexes for active FCM tokens, notification reschedule/delete, and stale processing reclaim |
| 20260609000005 | Add normalized food serving/nutrient tables, payout typed workflow fields, notification context schema version |
| 20260609000004 | Add normalized saved suggestion ingredients/steps and meal recipe instruction steps |
| 20260609000003 | Add normalized hydration_entries and backfill from legacy hydration meals |
| 20260609000002 | Add normalized user_profile_preferences and backfill profile JSON arrays |
| 20260609000001 | Add/align user ownership FKs for private user-owned tables |
| 047 | Add notification_sent_log for FCM deduplication |
| 046 | Add name_normalized to food_reference |
| 045 | Add challenge_duration, training_types (onboarding) |
| 044 | Widen firebase_uid to 128 chars |
| 043 | Add language_code to users |
| 037 | Add custom_protein_g, custom_carbs_g, custom_fat_g |
| 035 | Evolve barcode_products → food_reference |
| 034 | Add fiber, sugar to food_item and nutrition |

**Temporary compatibility fields:** `user_profiles` keeps legacy JSON array columns during read fallback, `saved_suggestions.suggestion_data` remains a raw compatibility snapshot, `meal.instructions` remains a recipe-step compatibility snapshot, `food_reference.serving_sizes` and `food_reference.extra_nutrients` remain legacy response snapshots, `payout_requests.payment_details` remains raw sensitive payout detail pending a security/contract pass, and legacy hydration meal rows remain readable during rollout. `notifications.context` is an immutable render snapshot only; recipient truth lives in `user_fcm_tokens`. Do not add new source-of-truth JSON fields without a documented exception in `docs/standards/db-api.md`.

**Production migration rule:** production schema creation is Alembic-only. `migrations/run.py` upgrades empty databases from base to head and refuses to stamp an existing unversioned schema automatically. Migration `001_initial_schema.py` is a real schema migration and should be reviewed like any later revision.

---

## Repository Pattern

**Smart Async:** Update existing or insert new record, with diff-based updates for nested entities.

**Eager Loading:** Pre-defined `joinedload` options per repository — avoids N+1 queries consistently.

**Session Scope:** Async sessions are scoped to FastAPI dependencies, background
handler UoW scopes, or explicit test fixtures. A session is never shared across
concurrent requests.

---

## Performance Optimization

- Indexes on `firebase_uid`, `status`, date columns, and `user_id` foreign keys
- `joinedload` / `selectinload` for known relationship paths
- Direct-pool defaults: 3 base + 2 overflow connections per worker, recycled every 120 seconds
- Redis cache-aside for frequently read data (see `external-services.md`)
- Local food search uses `food_reference.name_normalized`, a unique normalized-name
  constraint, and a `pg_trgm` GIN index. Results are verified-first,
  region/global scoped, bounded to 50, and deduplicated by normalized name.
- `scripts/import_food_seeds.py` imports non-barcoded scraper rows through the
  async unit of work using that normalized-name key. NIN and VN FCT sources are
  trusted ingredient references; manually verified rows remain protected. Run
  `--dry-run` first. The validator reports by default; `--fix` is required to
  rewrite source files.

---

See related: `system-architecture.md`, `external-services.md`, `code-standards.md`, `standards/db-api.md`
