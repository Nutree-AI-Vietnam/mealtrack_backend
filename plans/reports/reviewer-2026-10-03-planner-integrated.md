# Integrated weekly planner production review — 3 October 2026

## Scope

- Pending optimization batches 1–5 against `cbc3152297c5684e509769d0c226eea17d6409c5`; source changed during review by the lead/worker. Reviewed plan phases 01–05 and source callers, not only changed lines.
- Read-only source review; this report is the only reviewer-owned change. No live API/provider/deployment claims.
- Scout traced recipe summary consumers, selected swap validation, pantry/count reuse, owner/week/parent/slot locks, operation ledger, catalog publication triggers, provider admission/deadlines, preparation leases/results and GET localization.
- Protocol: scout first, spec compliance, critical + informational base/API checklists, focused verification.

## Overall assessment

The integrated read/generation/AI design preserves released default contracts and materially narrows database work. New output persistence is fenced and transactional. Initial AI shortlist/metadata/locale defects, worker layering gaps, preparation lease issue and load-harness query guard were corrected during review. No unresolved local code blocker remains in the reviewed paths. Live baseline, PostgreSQL recovery and release evidence remain separate gates.

## High priority

None remaining in reviewed application/worker implementation.

## Resolved load-harness finding

### Parse the load harness read-only query guard

The original `scripts/benchmarks/weekly_planner_load.py` guard tested the entire path for the substring `auto_generate=false`. Reviewer reproduced acceptance of `/v1/meal-plans/current?note=auto_generate=false`, `?auto_generate=false&auto_generate=true`, and `#auto_generate=false` with `allow_test_mutations=False`. These could still execute legacy auto-generation: unknown query keys are ignored, repeated values select the effective last value, and fragments never reach the server.

Verified correction: URL parsing rejects fragments and requires parsed `auto_generate == ["false"]` on read-only current-plan requests. Route method/shapes match declared planner/recipe groups; headers cannot override account authorization. Reviewer reran all three bypasses and an unrelated `/v1/users` mutation: all rejected. The legitimate `/v1/meal-plans/current?auto_generate=false` request was accepted. No HTTP requests or load executed. Distinct token environment variable names alone do not establish different authenticated accounts; dedicated fixture ownership remains part of run configuration.

## Resolved lease finding

The prior worker loaded the source snapshot after committing its claim, before starting its heartbeat. Initial expired/stolen-token correction added database-clock lease/token checks after the publication fence and mapping. A second scenario still needed immediate renewal: a 120-second lease that waited 110 seconds at the fence could start provider work with 10 seconds left, before the first 20-second heartbeat; another worker could reclaim it during the call. Final publication fencing protected stored data but did not preserve provider admission.

Verified correction: `run_once` opens a short heartbeat transaction immediately after snapshot loading and commits it before spawning computation. A failed renewal returns without any provider call. `CatalogPreparationClaims.heartbeat` uses `clock_timestamp()` to extend the lease from database wall clock. The new PostgreSQL `test_worker_renews_short_remaining_snapshot_lease_before_provider` forces one second remaining then asserts over 100 seconds remain at provider entry. Reviewer read this recovery test but did not execute its destructive isolated-DB fixture. The local code blocker is resolved by the source checks; actual PostgreSQL evidence remains a release gate.

## Informational

- `catalog_preparation_jobs` retains historical succeeded/superseded versions. Current claims filter/sort by status, available time and lease expiry, but the model/migration exposes only PK/uniqueness indexes. Capture EXPLAIN under realistic retained backlog before rollout; add measured claim/recovery indexes through the migration CLI if scans dominate. This is a throughput gate, not an asserted data-corruption defect.
- Fixed per-process interactive AI admission requires a pinned replica × worker × capacity allocation. The durable preparation queue has database-backed global admission. Autoscaling interactive API capacity remains gated on shared admission or an explicitly bounded deployment allocation.
- Publication-fenced mutation behavior is independently opt-in. Enable the planned flag combination coherently and prove source withdrawal versus PATCH/log races before rollout; do not infer those protections from projection activation alone.

## Acceptance evidence

| Requirement | Source conclusion / practical limit |
|---|---|
| GET defaults, current week, count flags | Route still defaults `auto_generate=True`, `include_grocery_count=True`; current-week timezone resolver preserved. Existing-plan read and explicit missing-plan 404 modes covered by contract tests. |
| Selected validation, allergies | Update fetches replacement IDs; hard preference changes additionally fetch every current unlogged ID. Full authoritative ingredients/allergen codes still feed the unchanged hard-filter. Missing/inactive recipes and logged slots reject. |
| Lean plan and groceries | Plan + slots loaders suppress unused catalog/pantry graphs; grocery projection receives only matching owner/plan snapshots and batches full selected ingredients. No concurrent awaits on one session. |
| Compact summary freshness | Versioned Redis keys use publication facets; withdrawals join active source rows; dirty/incomplete projections hydrate selected IDs or use authoritative full fallback. Optional Redis has one 100 ms read/write budget. |
| SQL Unicode/count/filter parity | Stored Python casefold values, escaped LIKE, C-collated tie order, canonical allergen resolution and fail-closed unknown preferences. Dirty query facets force complete fallback; dirty nutrition hydrates only page IDs without changing count. PostgreSQL tests exist; reviewer did not run destructive fixtures in the shared DB. |
| Synchronous mandatory query-field refresh | Standard seed insert/replacement, popularity-rank and admin-image publishers now acquire exclusive publication fencing before source writes, flush and rebuild in the caller's transaction. Rebuilder refreshes identity-map rows and replaces allergen links. The seed-import advisory lock has no reverse acquisition path from publication holders. Source/clean projection commit atomically; PostgreSQL conflict updates use wall-clock timestamps. Lead reports the real PostgreSQL publisher regression passed; reviewer inspected it without executing its destructive fixture. Direct SQL/dependency edits retain dirty authoritative fallback plus durable repair; post-edit performance still needs measurement. |
| Deterministic short generation | Off by default; ranking retains deterministic seed/tie order. Preflight releases UoW before selection. Final source fence → owner/week advisory/parent lock → ordered slots; compares source facets and plan/slot versions. Operation reservation/result complete in one final UoW. |
| Replay and cancellation | Existing completed operation returns owner-scoped plan; mismatched fingerprint conflicts; precomputation creates no claim. UoW cancels/rolls back final work. Missing-week/replay/cancel PostgreSQL tests are present. |
| Logging race / stale apply | Logging takes parent before slot and increments internal version; PATCH takes parent then ordered slot locks and rejects logged targets, while revision-aware swaps reject stale plan revisions. Generic optional `expected_revision` stays compatible. |
| AI authority and budgets | Dedicated purpose selects OpenAI, bypasses Cloudflare purpose prepending. Middleware bounds full AI request including auth to 30 s. Provider portion ≤25 s; SDK retries zero, one shared engine transient retry, one bounded semantic widening, cancellation releases admission. No proposal auto-apply. |
| Compact AI input and selected groceries | With projections enabled and an adjustment provider configured, proposal preparation calls selection candidates rather than the full ingredient graph. Hard validation uses authoritative compact selection features/allergen codes. After provider validation, a new UoW hydrates deduplicated proposal recipe IDs, bounded by the 14 slots, for canonical groceries. Flag-off and deterministic-providerless behavior retain legacy inputs. Explicit PATCH remains the mutation/revalidation boundary. |
| Named/current metadata/locale | Named eligible recipe precedes cap; corrected shortlist balances lunch/dinner and widens at most once. Current recipes travel separately from selectable candidates. Localized deterministic/fallback copy replaces the earlier English-only fragments. |
| Durable normal source mutations | Phase 5 row trigger inserts projection jobs inside source transaction; reconciliation rebuilds current facets and enqueues unique provider jobs. New generated migration stores immutable trigger snapshots and restores prior functions before dropping storage. |
| TRUNCATE durability | Initial gap corrected: producer now replaces the truncate invalidator too, enqueues surviving active recipes, and migration has matching upgrade/downgrade snapshots. Requires PostgreSQL regression proof. |
| Preparation output authority | Pure typed compute stages USDA updates; fenced result transaction checks source + claim, publishes versioned overlay/translations and completes job atomically. Unknown-provenance legacy estimates stay unavailable in new reads. Provider calls occur outside DB transactions. |
| GET preparation isolation | Persisted presentation flag uses DB reads/canonical fallback, including prepared English translations before the legacy English shortcut. Missing preparation falls back without a provider call; translation changes presentation fields only. Deprecated micronutrient compatibility endpoint remains cache-only. Retained auto-generation invokes its command contract. No new request-time provider authority introduced by persisted mode. |
| Existing layers | Preparation computer moved to app, composition to existing bootstrap, worker depends on domain protocol. Worker package registered; import-linter now analyzes it and keeps all four contracts. |
| Final instrumentation and defaults | Instrumented queue/null pools delegate `_do_get` unchanged to the existing SQLAlchemy parent. Class-based policy still selects queue-only capacity kwargs and preserves asyncpg pooler policy. Timers re-raise business errors/cancellation and swallow optional connector failures. Auth decorators preserve dependency signatures; reviewer resolved both signatures through installed FastAPI. Budget, selection CPU, localization, response projection and Redis now have actual phase boundaries. Example flags remain false, dedicated DB targets blank and capacity bounded. Relocated harness guard test resolves the real script by file path under default unit collection. |

## Edge cases found by scout

- Reproduced original 40 lunch-only shortlist omitting a valid dinner recipe; now corrected with meal-type coverage and bounded widening.
- Reproduced current slot `recipe_name=None` when its recipe fell outside shortlist; corrected with separate current metadata.
- Localized provider explanation previously appended English preference text; corrected with locale copy helpers.
- New worker folder initially escaped import-linter and imported app helpers from infra; corrected through app/bootstrap ownership and package registration.
- Direct TRUNCATE invalidated source facets without jobs; corrected producer/migration snapshots.
- Source-load wait could start a provider with an expired/stolen or near-expiry claim; token checks plus immediate wall-clock renewal now correct both cases.

## Verification and existing failures

- Fresh reviewer focused batches 1–4 regression run: **112 passed**, 1 warning, 1.77 s.
- Fresh reviewer preparation + adjusted service/adapter run after lead fixes: **50 passed**, 1.22 s.
- Focused Ruff source checks passed before the last lead edits; rerun in final gate.
- Fresh import-linter after worker layer correction: **4 kept, 0 broken**, 1,066 files / 5,198 dependencies.
- Generated phase 5 immutable upgrade/downgrade producer snapshots match the live row + TRUNCATE definitions exactly (AST value comparison); downgrade restores both prior invalidators. Bootstrap Ruff recheck passed after import cleanup.
- Final lease/shared-policy focused unit rerun: **74 passed**, 1.19 s; Ruff for worker, claims and load harness passed.
- Final compact AI/localizer/service/grocery/provider focused rerun: **63 passed**, 1.35 s. Ruff passed for both updated services and the three publisher/rebuilder files. Fresh import-linter: **4 kept, 0 broken**, 1,067 files / 5,205 dependencies.
- Late standard publisher review traced seed-import advisory locking, image generation caller, source triggers and rebuild transactions. No inverted seed-import/publication lock acquisition or new commit boundary found. Lead reports **64 unit**, **12 scoped mypy files**, and **1 real PostgreSQL publisher refresh** passed; these are attributed lead evidence, not reviewer executions.
- Final instrumentation review resolved `verify_firebase_token` and `get_current_user_id` through FastAPI's `get_typed_signature`: original request/security/cache dependency types and defaults remained intact. No database connection or HTTP request opened. Source traced pool delegation, policy kwargs, real phase call sites, blank dedicated-target handling and default unit harness import. No additional broad tests run; lead owns final suite.
- Isolated shared-policy experiment: initial proposal had two attempts after one 429, widened proposal then had one attempt, its budget was smaller than the initial budget, and admission returned to capacity two. Both calls execute on the caller task, so one ContextVar retry budget/deadline spans semantic widening.
- Load harness original bypasses reproduced, then fixed guards verified locally without HTTP requests or exposing tokens; no load executed. Final harness/worker/claims Ruff check passed.
- Fresh architecture run: **20 passed, 3 failed**. All three were verified in unchanged HEAD: direct commits in `web_funnel.py`, `web_funnel_redemption_session.py`, `admin_meal_catalog_import.py`; direct commit in `admin_meal_catalog_repository_async.py`; domain service count 111 against stale cap 46. HEAD already contains the exact offenders and 111 services. Do not characterize these as new optimization regressions or as a green architecture gate.
- Lead-provided earlier full unit snapshot: 3,413 passed, 79.80% coverage. Reviewer did not independently rerun full coverage and does not present this as final phase 5 evidence.
- Type coverage: unmeasured. Live latency, rows/bytes, provider quality/token/cache usage and deployment allocation: unmeasured.

## Recommended actions and plan status

1. Worker lease source issue and harness guard are resolved; run the new lease recovery test on the isolated PostgreSQL gate.
2. Run generated migration upgrade/downgrade and PostgreSQL source enqueue, TRUNCATE, lease/crash/reclaim/fencing tests on an isolated DB. Keep test truncation away from shared/live state.
3. Run final full unit/lint/import/architecture evidence after all integration edits, then phase 6 provider-isolated load/canaries and real environment baseline.
4. Phase implementation exists; do not mark phases 1–5 fully complete from local unit evidence. Pending plan criteria include deployed measurements, owner-ratified SLOs, PostgreSQL query/bytes proof, recovery evidence, quality/capacity allocation and rollout. Lead/project-manager owns plan mutations.

Docs impact: minor — report and operational worker/configuration guidance; no source/API documentation edit by reviewer.

## Unresolved questions

- Isolated PostgreSQL preparation recovery/rollback execution evidence.
- Live deployed worker/pool/provider settings, realistic catalog/backlog sizes, agreed latency/quality budgets and interactive quota allocation.

**Status:** DONE_WITH_CONCERNS
**Summary:** Integrated phases 1–5 and late compact AI, English preparation and standard publisher hooks reviewed. No unresolved local code blocker found; focused reviewer gates pass. Standard publisher synchronous refresh is resolved in source with lead-reported PostgreSQL proof.
**Concerns/Blockers:** Reviewer has not independently executed final PostgreSQL recovery/deployment gates. Direct SQL/dependency-edit fallback performance, deployed capacity and provider/load evidence remain release verification work.
