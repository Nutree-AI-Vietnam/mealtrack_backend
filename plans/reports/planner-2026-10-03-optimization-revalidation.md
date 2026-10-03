# Weekly planner optimization: checkout revalidation

Date: 2026-10-03. Scope: existing six-phase plan and source interfaces; no source edits or deployment verification. Reviewed `README.md`, `AGENTS.md`, the solution document and all phase files. Source checkout began at `cbc3152297c5684e509769d0c226eea17d6409c5`; the plan reviewed `00b6eaa6`. Working alongside implementation agents; their subsequent edits are outside this snapshot.

## Decision

Execute the existing sequence: 1 → 2 → 3, then stable-interface work in 4/5, then 6. No product decision blocks local implementation. Live baseline/SLO, provider evaluation, staging deployment and client/device proof remain distinct release gates. Do not mark them complete from local tests.

Only intervening commit is grocery day-line support (`cbc31522`, PR #690). Preserve the new table, `/v1/meal-plans/{id}/grocery-days` mutation, category/day grouping, quantity normalization, and separation from pantry/checkbox state. Add its fresh-read/replay cases to combined verification. Existing plan line references moved; use symbols below.

## Source facts to keep

| Fact | Verified source | Execution consequence |
|---|---|---|
| Current GET defaults auto-generation/count to true | `src/api/routes/v1/meal_plans.py:114` | Keep released-client behavior; explicit false still gives read-only 404. |
| Count launches another grocery query, which reloads plan | `meal_plans.py:_plan_response`; `weekly_meal_planner_query_handlers.py:GetWeeklyGroceriesQueryHandler` | Reuse detached owner-validated plan via application service/query; no ORM/session transfer. |
| Plan mapper needs persisted slot state, discards joined catalog/pantry graph | `weekly_meal_plan_repository_async.py:_load/_to_domain`; `weekly_meal_plan_slot.py:catalog_meal` | Override relationship loading per operation before changing defaults globally. |
| Summary batch already omits authoritative misses without per-ID fallback | `weekly_recipe_service.py:summaries` | Keep behavior; grocery calculation still performs miss fallback. |
| Browse enforces canonical allergies and Python casefold order | `weekly_recipe_service.py:list` | Prior memory that browse discarded allergies is stale. SQL parity must retain unknown-allergen fail-closed behavior. |
| Generation/update/AI still load full active catalog | `weekly_meal_plan_service.py:generate/update/ai_proposal` | Compact candidate and selected-ID validation remain necessary. |
| Existing owner/week lock covers existing row only | `weekly_meal_plan_repository_async.py:lock_user_week` | Add stable advisory serialization for absent-week creation; keep uniqueness. |
| Logging increments slot version separately | `weekly_meal_plan_repository_async.py:mark_slot_logged` | Capture/revalidate slot versions; preserve public plan revision behavior. |
| Active catalog revision is aggregate count/timestamps | `catalog_recipe_repository_async.py:get_active_catalog_revision` | Replace hot aggregate with publisher-controlled revision only after all writers participate. |
| Proposal adapter uses first 200 candidates and `general` | `weekly_meal_plan_adjustment_provider.py:propose`; `model_purpose.py`; `meal_generation_service.py:PURPOSE_MAP` | Explicit planner purpose/shortlist/deadline must cover enum, mapping, manager and adapter. |
| Detail reads cached enrichment; providers own inner claims/UoWs | `weekly_recipe_service.py:detail`; `catalog_recipe_micronutrient_enrichment_service.py` | Preserve cache-only detail; extract typed computation before atomic job/result finalization. |
| Neon engine branch omits queue-pool arguments and logs NullPool | `config_async.py:91` | Baseline owner fixes kwargs/logging based on actual pool class. |

## Interface and ownership agreement

Main integrator owns existing weekly services/routes/query handlers, repository ports/implementations, model registry, `AsyncUnitOfWork`, dependency/bootstrap wiring, migration coordination and publisher protocol integration. Baseline agent owns pool configuration, metric/request logging and its pool/API guard tests. No concurrent edits to these boundaries.

| Independent responsibility | Deliverable handed to integrator |
|---|---|
| Catalog projection | Typed summary/candidate/ingredient/detail results; normalized matching columns; dependency-facet digest/rebuilder; detached immutable inputs. |
| Deterministic selection | Candidate-compatible pure selection with exact seed/tie/calorie/constraint parity; existing algorithm version retained. |
| AI policy | Dedicated purpose policy, eligible/named shortlist, locale response, monotonic deadline and finite admission/retry policy; ports carry deadline/locale explicitly. |
| Preparation worker | Job/translation models and ports, short leased claims, heartbeat/fencing, typed compute outcome, worker-owned atomic completion, bounded reconciliation. |
| Test owner | Tests only; reads implementation, leaves shared source to integrator. PostgreSQL fixture mutations must run sequentially. |
| Reviewer/docs | Review final integrated source; update evergreen docs from achieved behavior and preserve unfulfilled release gates. |

Required interfaces before splitting 4/5:

- Summary projection is additive to current full `CatalogMeal` loader. Do not narrow `get_meals()` globally while groceries/logging/enrichment still require ingredients/nutrition. Prefer explicit `get_summaries(ids)`, `get_candidates(...)`, `get_ingredients(ids)` and detail operations.
- Read plan stays detached `WeeklyMealPlan`. Grocery calculation already accepts `(uow, plan)`; reuse that entry through owner-scoped application orchestration instead of rereading plan.
- Version contract separates selection/macros, ingredients, translation and micronutrients. Versions include relevant canonical values/contracts and stable allergen/conversion eligibility; micro-only updates do not invalidate macro selection.
- Publication transaction takes revision-row exclusive lock before authoritative changes, updates mandatory eligibility/query fields and inserts jobs atomically. Final generation uses publication shared lock → stable owner/week advisory lock → ordered slot locks → operation/plan validation and atomic commit.
- Preparation compute returns ready/retryable/permanent/superseded plus validated payload and staged canonical updates. It cannot commit, conceal failure or own an independent claim.
- Deadline starts at endpoint entry. Planner SDK retries zero; at most one engine transient retry fits provider ≤25s and total ≤30s. Admission capacity must be computed over actual replicas × API processes and separately allocated worker traffic.

## Publisher inventory and missing coverage risk

The plan names import/admin paths, but catalog dependencies are mutated elsewhere. A revision-row lock in generation alone cannot provide source safety. Integrator must route these through one protocol (or deliberately retain authoritative fallback until covered):

| Producer | Authoritative changes |
|---|---|
| `catalog_meal_seed_import_service.py` → `catalog_recipe_repository_async.py:add_seed_meal/update_popularity_rank` | Create/replace seed rows, ingredients, steps, publication/allergen links, rank. Existing `lock_seed_import()` is import-only. |
| `admin_meal_catalog_import.py` → `catalog_food_reference_review_service.py` → `food_reference_repository_async.py:approve_for_catalog_seed` | Reference approval/publication eligibility. Admin commits the dependency session. |
| `admin_meal_catalog.py` → `admin_meal_catalog_repository_async.py:set_image_url` | Presentation image mutation with repository-owned commit. |
| `food_reference_repository_async.py:upsert/upsert_seed/upsert_by_normalized_name/_sync_normalized_children` | Macros, density/servings, source/verification and normalized nutrition children. |
| `food_reference_adopt.py:adopt_provider_food/_apply_nutrition` | Provider reference adoption and replacement of nutrition/serving relationships. |
| `food_reference_integrity_repository.py:materialize_reference/quarantine_reference/restore_reference/activate_policy` | Public reference eligibility/policy generation; revision fanout must account for linked recipes. |
| `catalog_recipe_micronutrient_enrichment_service.py:_hydrate_linked_fdc_references` → `update_usda_micronutrients` | Canonical micros before overlay persistence; intentional output version must not supersede itself. |
| `scripts/import_catalog_recipe_seeds.py` | CLI importer goes through same catalog repository; retain transactional publishing. |
| `scripts/generate_catalog_meal_images.py`, `scripts/estimate_catalog_macros_ai.py:update_database`, `scripts/data/update_catalog_macros.sql` | Direct catalog image/payload writes bypass app publisher; adapt, gate or require explicit repair/reconciliation before projection cutover. |

Search additional administrative/maintenance SQL before release. Database triggers can cover external writes only if implemented with the same version/facet semantics and tested; do not assume a Python hook sees all mutations.

## Migration and PostgreSQL strategy

- Local tools verified: Homebrew PostgreSQL/psql 14.17 (`/opt/homebrew/bin`), `initdb`, `pg_ctl`, `pg_isready`; project Python 3.13.2; `ck` available. Alembic one head: `20261002103128257288`.
- Parent created disposable PostgreSQL at `127.0.0.1:55439`, DB `planner_optimization_test`, user `planner_test`. `pg_isready` accepts connections; `alembic_version` is current head. Existing model schema was created/stamped before source model edits, enabling a real old→new migration test. Do not record connection secrets/URLs in tracked artifacts.
- Use `.venv/bin/python migrations/cli.py generate "catalog projection and preparation"` or put `.venv/bin` first in PATH before wrapper. `migrate.sh` invokes bare `python`; system runtime is unsuitable. Set disposable `DATABASE_URL_DIRECT` and `DATABASE_URL` in execution environment. `migrations.utils` ignores `MIGRATION_DATABASE_URL`, while `init_postgres_db.py` prioritizes it; clear or override any inherited value to keep both tooling paths on the same disposable DB.
- Register all new models before CLI generation; review autogenerated upgrade/downgrade for unrelated metadata drift, constraints, non-null sentinel locale uniqueness and typed indexes. One integrator generates migrations to avoid duplicate heads.
- Expand schema first; restartable bounded backfill uses captured source versions and parity checks. Legacy readers remain until complete. Old estimates with unknown provenance cannot simply inherit fresh version keys.
- `scripts/init_postgres_db.py` bootstraps an empty DB with current metadata then stamps head; it does not replay the new migration from an older schema. Keep the pre-edit database for actual upgrade/downgrade verification.
- `tests/integration/postgres/conftest.py` requires `TEST_DATABASE_URL` with asyncpg scheme and truncates catalog/users with CASCADE. Dedicated disposable DB only; serialize suites/fixtures and never use staged real accounts.
- Local PostgreSQL tests: real query/bytes/load/EXPLAIN, missing-week same/different keys, publication races, swap/log/slot-version conflict, worker claim/reclaim/fencing, migration up/down/up. Local evidence does not establish staging RTT, Neon pooling or live provider latency.

## Execution TODO and completion gates

- [ ] 1: Characterize legacy GET/count/error/timezone/replay contracts; correct class-based pool kwargs; measure local PostgreSQL stage/query/rows evidence. Record live deployed config/baseline as still pending until obtained.
- [ ] 2: Lean plan/lock loaders; additive selected-summary loader; replacement-only validation plus all unlogged IDs on preference changes; grocery plan reuse and bounded missing IDs; preserve day lines.
- [ ] 3: Finalize facets/publisher inventory; registered CLI-generated projection/revision schema; bounded backfill/parity and SQL browse; compact generator; absent-week advisory lock and final selected/slot validation with short atomic operation commit.
- [ ] 4: Explicit OpenAI purpose/isolated clients; shortlist retaining eligible named recipes; locale explanation; endpoint deadline/retry/admission cancellation; zero unsafe proposals and no late write. Live quality/cost/tokens remain a separate evaluation.
- [ ] 5: SQL-authoritative preparation jobs/translations; typed compute extraction; lease/fence/atomic completion; canonical pending fallback and versioned optional cache budget; replace legacy claims/background work only after producer/backfill/recovery readiness.
- [ ] 6: Compile changed modules, focused tests, CI unit coverage ≥65%, Ruff/type/import boundaries, real PostgreSQL concurrency/migration gates, degradation/worker kill tests. Then agreed mixed-load, limited staging canaries, rollback rehearsal and fresh-read/device persistence gates.

Docs impact: major when implementation is achieved; this report only revalidates handoff. Existing plan statuses and mobile release-blocker gates remain pending.

Unresolved measurements: deployed pool/provider/index configuration and SLO; actual catalog/traffic/region/replica allocation; fanout/backfill duration; supported-client adoption; AI quality/shortlist tuning; worker/global quota and readiness; staging/device evidence. No unresolved product choice identified.
