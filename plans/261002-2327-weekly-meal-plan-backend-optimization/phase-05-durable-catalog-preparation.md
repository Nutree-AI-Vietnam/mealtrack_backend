---
phase: 5
title: "Durable Catalog Preparation"
status: in_progress
effort: ""
priority: P1
dependencies: [3]
---

# Phase 5: Durable Catalog Preparation

## Overview

Durable Python preparation, source producers and persisted presentation implemented; local PostgreSQL recovery passed. Worker deployment/readiness/load remain pending.

## Context Links

- [Solution](./solution-document.md), [dependency revisions](./phase-03-catalog-projections-and-generation.md), [AI/provider isolation](./phase-04-ai-request-policy.md).
- [Performance review](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/reports/meal-plan-performance-review-2026-10-02.md), [DB/API rules](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/standards/db-api.md).

## Key Insights

- FastAPI post-response tasks run inside API processes; per-invocation admission and 0.5-second pending polling do not bound system-wide provider work.
- Existing micronutrient rows are keyed by recipe/content_hash and have inner claims; the service owns UoWs and catches failures. Calling it unchanged cannot make overlay/job completion atomic. Keep its value storage, migrate freshness to dependency/contract versions, and refactor compute/persistence boundaries.
- Node `nutreeai_async` owns a separate recommendation contract and is not automatically the weekly-planner worker.

## Requirements

- Insert durable preparation jobs in the same transaction as catalog/dependency mutation. Optional queue wakeup is never the sole authority; periodic worker claims recover lost wakeups.
- Typed job state follows pending→running→succeeded, retry_wait/failed/superseded outcomes; retain attempts/backoff, lease/heartbeat, fencing token and unique task+recipe+dependency version+non-null locale sentinel+contract version.
- Persist recipe translations by dependency version and locale/field contract; GETs use DB/cache only with canonical/pending fallback and never enqueue. Catalog/generate/update commands enqueue missing work; worker reconciliation/backfill repairs missing versions.
- Redis is optional with initial proposed ≤100 ms total read/write budget and versioned keys. Grocery caching waits for complete plan/pantry/interaction revision invalidation.

## Architecture

Catalog transaction → durable SQL job row → worker `SKIP LOCKED` lease claim → bounded provider admission/deadline → fenced result/job completion. The job row itself is the durable queue; no second outbox is needed. Provider calls run outside DB transactions; heartbeat/lease recovery prevent old workers overwriting new versions. Persist micros in existing storage and versioned translations; GETs never await preparation.

## Related Code Files

- Modify/reuse: [enrichment service](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_recipe_micronutrient_enrichment_service.py:133), [existing micronutrient model](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/meal_recommendation/catalog_micronutrient_enrichment.py:22), [catalog claims/results](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/catalog_recipe_repository_async.py:243).
- Modify: [response localizer](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_meal_response_localizer.py), [weekly recipe reads/cache](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/weekly_recipe_service.py:119), [Redis adapter](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/cache/redis_client.py).
- Modify through integrator: [catalog import](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/app/services/catalog_meal_seed_import_service.py:322), [catalog mutation repository](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/repositories/admin_meal_catalog_repository_async.py), [model registry](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/models/__init__.py), [UoW](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/uow_async.py).
- Modify through integrator: [API background/detail compatibility paths](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/routes/v1/meal_plans.py:288), [provider dependency wiring](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/base_dependencies.py), [best-effort publisher reference](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/adapters/best_effort_integration_event_publisher.py).
- Extend: [enrichment tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_catalog_recipe_micronutrient_enrichment_service.py), [localizer tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/app/services/test_catalog_meal_response_localizer.py).
- Create: focused job model/port/repository, translation read model, worker claim/heartbeat runner, generated migrations, preparation backfill and recovery tests in existing clean layers. Delete: no public route/table; retire API polling/background execution after cutover.

## Implementation Steps

1. Specify job uniqueness/status/check constraints, nullable-result semantics, timestamps and deletion ownership; use non-null locale sentinel for nontranslation jobs.
2. Generate/register schema and downgrade through CLI; add job insertion to every catalog/dependency mutation transaction and bounded idempotent readiness backfill.
3. Implement short `SKIP LOCKED` claims with token/lease/heartbeat; execute providers outside transactions, cap worker/replica admission, and size provider deadline within lease renewal rules.
4. Extract provider computation returning typed ready/retryable_failure/permanent_failure/superseded result, validated payload and staged canonical nutrient updates, without inner claims/commits or swallowed failures. Worker-owned final UoW fences input/job token, applies intentional canonical updates through publisher locking, writes matching output-version overlay and completes atomically. Define resulting dependency facet so intentional updates do not supersede their own result.
5. Extend existing micronutrient table with dependency/contract versions and uniqueness via expand/backfill; unknown-provenance legacy estimates stay pending in new reads. Job lease is new execution authority; migrate old schedulers to the compatible claim protocol before retiring inner claims/polling. Commands enqueue missing versions; GETs read only; worker reconciliation repairs gaps. Retry transient results with finite backoff/jitter and retain exhausted/permanent failure for controlled replay.
6. Persist only presentation fields needed by list/detail/plan/groceries under explicit locale/version keys; serve authored/prepared translations and canonical fallback without surprise translation-provider calls.
7. Move generated-plan preparation off API BackgroundTasks; optional queue publication may wake worker after commit, but SQL polling recovers publish loss/restart.
8. Apply one total optional Redis budget across MGET/DB fallback/write decisions; skip writes during degradation. Keep grocery cache off until pantry/interaction revision invalidation is available and tested.
9. Compile; test duplicate jobs, crashes before/after result commit, lease expiry/reclaim, stale fencing, dependency changes, poison jobs, retry exhaustion and worker/provider shutdown.
10. Measure queue age/readiness/throughput and isolate worker DB/provider capacity; roll out behind flags only with phase 6 recovery evidence.

## Todo List

- [x] Build SQL-authoritative job insertion/claims/recovery and generated schema.
- [x] Persist versioned translations and keep existing micro values authoritative.
- [ ] Cut GET/provider/polling and API background dependence after readiness backfill.
- [ ] Bound worker providers/DB and optional Redis; exercise kill/replay/fencing.

## Success Criteria

- [x] Lost optional wakeup or killed API process cannot lose committed jobs; retries/claims cannot publish stale results.
- [x] Provider computation has typed failure outcomes and no hidden result commits; overlay/job completion shares one UoW, dependency freshness is proved, and unverifiable legacy results are not falsely marked ready.
- [x] Multi-replica provider work stays within declared capacity; no lease is claimed then abandoned in unbounded admission.
- [ ] Detail/list/plan/grocery GETs perform zero translation/nutrient provider calls or preparation job inserts and return compatible canonical/pending data; retained legacy auto-generation invokes its command contract.
- [ ] Existing deprecated micronutrient route remains cache-only; Redis off/down does not prevent correct reads or writes.

## Risk Assessment and Security

- Job/result version mismatch can poison caches: fence by both claim token and authoritative dependency version.
- Worker consumes DB/provider quota: reserve separate budgets and measure combined API/worker capacity before scale-out.
- Failed-job replay may repeat costly calls: constrain operator controls, deduplicate uniqueness keys, and store sanitized error classes rather than private content.

## Unresolved Measurements and Next Steps

- Unknown: mutation/job rate, locale coverage, readiness lag, worker size, global quotas, lease/heartbeat durations, retry limits and Redis hit/value size.
- Phase 6 verifies migration/rollback/recovery and limited canaries. Queue transport choice can remain optional because durable SQL rows determine correctness.

## Verified Local Progress — 2026-10-03

- Generated job/translation schema plus nullable micro provenance expands existing storage. Row and TRUNCATE producers insert durable projection work atomically with source mutation; matching exact-facet provider jobs are created by reconciliation.
- Global SQL claim admission, SKIP LOCKED tokens, wall-clock lease checks, immediate renewal, heartbeats, finite retry/exhaustion, provider cancellation and atomic result/job publication are tested. Provider computation holds no DB checkout.
- Prepared micros use facet+contract authority; unknown legacy provenance stays pending. Intentional USDA updates publish the resulting facet atomically; cosmetic legacy-hash edits preserve ready overlays.
- Versioned recipe translations include English/Vietnamese authored-source routing. Enabled presentation GETs read prepared/authored values or canonical fallback without provider calls/job insertion; legacy GET auto-generation retains its command behavior.
- API post-response enrichment retires only behind the durable flag; old schedulers enqueue under that flag. Worker/backfill use reserved pools and bounded batches; Redis has one optional100ms budget.

Evidence: [integrated report](../reports/implementation-2026-10-03-weekly-planner-optimization.md). Open composite acceptance criteria stay unchecked; local implementation does not establish release completion.
