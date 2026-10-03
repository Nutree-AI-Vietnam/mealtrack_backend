---
phase: 1
title: "Baseline And Contract Guards"
status: in_progress
effort: ""
priority: P1
dependencies: []
---

# Phase 1: Baseline And Contract Guards

## Overview

Instrumentation, compatibility guards and pool policy implemented; local checks passed. Deployed baseline/capacity measurement remains pending.

## Context Links

- [Solution and budgets](./solution-document.md); [performance review](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/reports/meal-plan-performance-review-2026-10-02.md).
- [README](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/README.md), [DB/API rules](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/standards/db-api.md), [testing standards](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/docs/testing-standards.md).
- [Mobile release reference](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/260930-1204-weekly-planner-release-blockers/plan.md); keep its pending gates unchanged.

## Key Insights

- Review SQL counts use isolated SQLite; CPU measurements exclude ORM/HTTP/providers. Deployed catalog size, latency, geography, routing, and actual pool settings remain unknown.
- The route defaults GET auto-generation and counts to true; Flutter currently sends auto-generation true and counts false. Compatibility guards must precede read-path changes.
- `config_async.py` branches on mode while Neon policy can select a queue pool; requested capacity can differ from the actual engine.

## Requirements

- Functional: preserve request/response/error contracts, current-week generation, owner scope, replay, revisions, logged slots, hard constraints, backend calories, and pantry/Undo persistence.
- Non-functional: measure phase timings, statements, rows/bytes, CPU/event-loop delay, memory, and concurrent resource demand without logging private payloads.
- Keep observed values, local experiments, and proposed targets in distinct evidence fields; release owner ratifies targets after baseline.
- Provisional server p95 goals under agreed load: saved plan/no count 300 ms, recipe page 400 ms, groceries 500 ms, generation 1.5 s; AI terminal response within 30 s. Use existing release SLO where documented.

## Architecture

Instrument the awaited route → CQRS handler → service → UoW/repository path, plus background completion separately. Record one correlation ID and bounded stage labels. Select engine kwargs from the actual pool class: queue settings for `AsyncAdaptedQueuePool`, compatible kwargs for `NullPool`; preserve asyncpg/PgBouncer policy.

## Related Code Files

- Modify: [routes and response projection](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/routes/v1/meal_plans.py:108), [request timing](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/api/middleware/request_logger.py:71), and [UoW](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/uow_async.py:102) for phase spans.
- Modify: [engine construction](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/config_async.py:91), [connection policy](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/src/infra/database/connection_policy.py:140), [pool tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/infra/database/test_config_async.py).
- Extend: [planner contract tests](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/unit/api/test_weekly_meal_planner_contract.py), [load harness](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/tests/performance/locust_meal_catalog.py).
- Create: aggregate baseline evidence under this plan's reports; no schema migration expected. Delete: none.

## Implementation Steps

1. Pin deployed backend/client revisions, data snapshot, catalog count/diversity/bytes, locale mix, cache state, DB geography, worker count, routing, and actual engine settings.
2. Capture contracts and regression scenarios before edits, including missing-plan GET with `auto_generate` true/false and count true/false, timezone week edges, replay/conflict, and unauthorized access.
3. Add auth, budget, checkout, plan/catalog SQL/map, selection CPU, lock/commit, Redis, localization, AI attempts, and background spans; distinguish response start from preparation completion.
4. Measure existing/missing/confirmed plans, en/vi cold/warm reads, grocery-count flags, catalog sizes, and realistic concurrency; include p50/p95/p99, errors, timeouts, and rows/bytes.
5. Fix class-based engine argument assembly; assert actual size/overflow/timeout/recycle/pre-ping and prepared-statement policy for direct, Neon queue, and Neon NullPool configurations.
6. Re-measure changed pool behavior without resizing workers; compare configured/reported/actual capacity and checkout waits. Compile changed files, run pool/contract tests, and review final code.
7. Publish aggregate baseline and owner-approved targets; hand read-path fixtures and evidence to phase 2.

## Todo List

- [ ] Record deployment/data/environment matrix and contract snapshots.
- [ ] Add safe phase spans and collect actual SQL/bytes/capacity measurements.
- [x] Correct conditional engine configuration and cover all pool classes.
- [ ] Ratify proposed budgets against baseline and pass focused regressions.

## Success Criteria

- [ ] Every comparison identifies revision, locale, cache state, data size, count flags, concurrency, and excluded phases.
- [x] Engine assertions reflect actual queue/NullPool configuration; no false NullPool log or changed asyncpg safety policy.
- [ ] Contracts and persisted-state invariants pass; no claim of live success derives from SQLite or unit tests.

## Risk Assessment and Security

- Timing hooks can distort hot paths: sample and compare instrumentation overhead. Protect auth IDs, bearer tokens, prompts, ingredients, and connection URLs; commit aggregates only.
- Pool correction can increase concurrent connections: model API plus separate-worker capacity against Neon limits before enabling changed settings.
- Generation/mutation benchmarks alter state: isolate test users, replay keys, and cleanup without deleting unrelated data.

## Unresolved Measurements and Next Steps

- Unknown: actual deployed pool/provider configuration, catalog/data distributions, endpoint/provider percentiles, lock waits, event-loop lag, and agreed SLOs.
- Phase 2 starts after baseline and contract guards; phase 6 owns full CI/load/release evidence. This phase is planned, not executed.

## Verified Local Progress — 2026-10-03

- Safe spans now cover auth verification/lookup, budget, pool acquisition, plan/catalog SQL, mapping/selection, publication/row locks, commit, Redis, localization, AI admission/attempts and response/request completion. Pool acquisition includes queue wait plus establishing a connection; nested spans are not additive endpoint percentiles.
- Queue/NullPool subclasses preserve parent behavior; actual constructed classes and queue bounds are logged. PostgreSQL one-connection wait test proves acquisition timing without changing size/overflow.
- Default GET auto-generation/count flags, ownership/error shapes and backend calories remain compatible. Configured capacity values are tests, not live allocation evidence.

Evidence: [integrated report](../reports/implementation-2026-10-03-weekly-planner-optimization.md). Open composite acceptance criteria stay unchecked; local implementation does not establish release completion.
