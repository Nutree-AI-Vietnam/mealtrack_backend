---
title: "Worker-First Meal Cache And Insight Cutover"
description: "Finish the direct-Queue cross-repo migration by keeping meal cache invalidation and meal value insight generation on the Worker path behind one generic meal event."
status: in_progress
priority: P1
effort: "6-7d"
branch: "architecture/optimize-architecture"
tags: [feature, backend, infra, worker, cache, insights]
blockedBy: [260823-1056-generic-integration-event-redesign]
blocks: [260822-1730-cloudflare-async-cache-projection-worker]
created: "2026-08-24"
createdBy: "ck:plan"
source: skill
---

# Worker-First Meal Cache And Insight Cutover

## Overview

Current branch already publishes direct Queue events through
`CloudflareQueuePublisher.publish` (`src/infra/adapters/cloudflare_queue_publisher.py:93-137`).
Meal cache invalidation is therefore already Worker-first for the generic meal
event path: backend meal mutations emit `meal.created.v1` /
`meal.updated.v1` / `meal.deleted.v1`
(`src/app/events/meal/meal_events.py:10-27`), the Worker routes those events
through `IntegrationEventRouter` and `MealCacheInvalidationHandler`
(`../nutreeai_async/src/interfaces/cloudflare/integration-event-router.ts:52-149`,
`../nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts:141-195`,
`../nutreeai_async/src/application/event-handlers/meal-cache-invalidation-handler.ts:12-88`).

The old outbox-based cache-worker plan is no longer executable on this branch.
`AsyncUnitOfWork` no longer wires `outbox` or `affiliate_outbox`
(`src/infra/database/uow_async.py:137-141`), and the outbox model/dispatcher
files referenced by `plans/260822-1730-cloudflare-async-cache-projection-worker`
are already deleted from the worktree.

Meal value insights were the remaining process-local gap. FastAPI now embeds a
bounded insight snapshot in the same generic `meal.created.v1` or
`meal.updated.v1` event after committed meal writes; the old scheduler and AI
port are removed. The Worker routes one event to cache invalidation and, when
the snapshot is present, the application-layer insight business handler. It
writes validated results to `meal_insight:{meal_id}` for seven days before the
Queue message is acknowledged.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Contract Reconciliation](./phase-01-contract-reconciliation.md) | Completed |
| 2 | [Backend Cache Cutover](./phase-02-backend-cache-cutover.md) | Completed |
| 3 | [Backend Insight Publishing](./phase-03-backend-insight-publishing.md) | Completed |
| 4 | [Worker Insight Cache And Push](./phase-04-worker-insight-cache-and-push.md) | Completed |
| 5 | [Verification And Rollout](./phase-05-verification-and-rollout.md) | In progress |

## Dependencies

- Depends on `260823-1056-generic-integration-event-redesign`: direct Queue
  publish, generic `IntegrationEvent` envelope, and Worker orchestration are
  the baseline this plan extends.
- Blocks `260822-1730-cloudflare-async-cache-projection-worker`: Phase 5 of the
  older outbox-based plan must not be resumed as written on this branch.
- Phase ownership is intentionally non-overlapping:
  Phase 2 owns backend meal cache event emitters/tests;
  Phase 3 owns backend insight publication helpers/routes/graph wiring;
  Phase 4 owns only `../nutreeai_async/**` insight cache/push files;
  Phase 5 owns docs, staging proof, and rollback notes.

## Scope Locks

- Keep the dirty worktree intact. No revert/reset/overwrite of unrelated user edits.
- Treat the Worker's generic meal-event path as authoritative for durable cache
  invalidation.
- Treat the Worker's bounded `data.insight` snapshot parser as authoritative;
  only additive fields are allowed in this slice.
- Separate local proof, CI proof, staging proof, deployment proof, and live FCM
  proof in every report.
- The meal-insight path is reader-only in FastAPI. Generic query cache-aside
  writers remain unchanged because they serve projections outside this Worker
  insight contract.
- Generic meal invalidation removes or fences the Worker-owned insight key when
  `data.insight` is present (and always for deletes). Insight-neutral updates,
  such as photo-only changes, preserve an existing insight result.
