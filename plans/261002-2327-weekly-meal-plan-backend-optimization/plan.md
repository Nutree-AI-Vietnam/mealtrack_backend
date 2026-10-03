---
title: "Weekly Meal Plan Backend Optimization"
description: "Reduce weekly-planner read, generation, AI, and catalog-preparation cost while preserving contracts and persisted correctness."
status: in_progress
priority: P1
branch: "delivery"
tags: [backend, database, api, refactor, critical]
blockedBy: []
blocks: []
created: "2026-10-02T16:27:46.699Z"
createdBy: "ck:plan"
source: skill
---

# Weekly Meal Plan Backend Optimization

## Overview

Implementation and local verification executed against checkout `cbc31522` on `delivery`. All six batches are integrated. Release evidence remains pending; all five new cutover flags default off.

Read the [solution document](./solution-document.md) for architecture, proposed budgets, schema contracts, failure recovery, and rollout decisions. The [performance review](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/reports/meal-plan-performance-review-2026-10-02.md) supplies source traces and qualified SQLite/CPU measurements.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Baseline And Contract Guards](./phase-01-baseline-and-contract-guards.md) | Implemented; local checks verified; release gates open |
| 2 | [Narrow Reads And Batching](./phase-02-narrow-reads-and-batching.md) | Implemented; local checks verified; release gates open |
| 3 | [Catalog Projections And Generation](./phase-03-catalog-projections-and-generation.md) | Implemented; local checks verified; release gates open |
| 4 | [AI Request Policy](./phase-04-ai-request-policy.md) | Implemented; local checks verified; release gates open |
| 5 | [Durable Catalog Preparation](./phase-05-durable-catalog-preparation.md) | Implemented; local checks verified; release gates open |
| 6 | [Verification And Rollout](./phase-06-verification-and-rollout.md) | Implemented; local checks verified; release gates open |

## Dependencies

- Sequence: 1 → 2 → 3; phases 4 and 5 consume phase 3 interfaces; phase 6 gates the combined release.
- Backend owners: baseline/pool engineer (1), read-path engineer (2), catalog/generation engineer (3), AI engineer (4), worker engineer (5), release owner (6). Scoped baseline/catalog/preparation/test/review owners executed the batches.
- An integrator owns overlapping routes, application services, UoW/bootstrap wiring, and migration coordination. Independent owners submit focused modules; do not edit shared files concurrently. Test owner owns tests; reviewer reviews final integrated code.
- [Original planner plan](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/260921-0000-weekly-meal-planner-backend/plan.md) is a design reference with stale pending metadata, not proof that the implemented backend is missing.
- [Release-blockers plan](/Users/alexnguyen/Desktop/Nut/mealtrack_backend/plans/260930-1204-weekly-planner-release-blockers/plan.md) remains the mobile/device/persistence release reference. Preserve its open gates and existing status; this handoff does not complete them.
- No cross-plan blocking metadata is invented. Baseline deployment facts, capacity limits, and latency targets must be measured and recorded before cutover.

## Invariants

- Keep FastAPI → CQRS/application → pure domain → infrastructure ports; public paths, response shapes, error semantics, ownership, revisions, idempotency, logged slots, and backend calorie authority remain compatible.
- Explicit action generates the current Monday-based week deterministically without an LLM, persists it, then reloads from DB. Preserve existing GET `auto_generate=true` defaults and current Flutter callers until client rollout and usage evidence support deprecation.
- Preserve publication/nutrition eligibility, hard diet/allergy constraints, grocery/pantry semantics, and Undo verified by fresh reads/reopen. Projection/cache data is rebuildable from authoritative catalog dependencies.
- AI remains a proposal with `base_revision`; explicit PATCH applies it. Interactive requests use OpenAI standard API under one deadline; optional Batch is offline work only.
- SQL job rows are durable preparation authority, consumed by a separate Python worker; queue messages may wake it. Catalog GETs use persisted translations/micros with canonical fallback.

## Completion Evidence

- [ ] Real PostgreSQL query/bytes/lock/pool baselines and agreed percentile targets recorded for the deployed revision.
- [ ] Scoped regressions, CI unit coverage ≥65%, architecture/lint, migration/backfill/rollback, concurrency, degradation, and worker recovery gates pass.
- [ ] Mixed-load and limited staging canaries pass; client compatibility and persisted reopen/Undo gates are linked to exact revisions.
- [ ] Deployment, mobile validation, and preparation readiness are reported separately. Docs impact: major; implementation, local evidence and reversible rollout procedures are recorded in active docs/reports. Deployment/device readiness remains open.

## Local Evidence — 2026-10-03

- [Integrated results and changed-file manifest](../reports/implementation-2026-10-03-weekly-planner-optimization.md).
- 3452 unit tests passed,79.51% coverage;40 PostgreSQL tests passed. Four import contracts and changed-file Ruff/compile pass. Existing architecture/global typing debt remains red and is documented.
- Selection CPU improved87.6% on the10k synthetic fixture; plan core SELECTs8→2. Neither figure is a deployed endpoint percentile.
- Direct/shared dependency synchronous projection coverage, fixed interactive deployment allocation, readiness backfill, authenticated mixed load/provider canaries and device reopen/Undo remain cutover gates.
- Production deployment/migrations were not performed.
