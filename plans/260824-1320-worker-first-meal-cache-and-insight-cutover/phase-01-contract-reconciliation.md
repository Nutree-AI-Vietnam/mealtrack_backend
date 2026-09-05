---
phase: 1
title: "Contract Reconciliation"
status: completed
priority: P1
effort: "0.5d"
dependencies: []
---

# Phase 1: Contract Reconciliation

## Overview

Freeze the authoritative contracts before code changes. This phase exists to
stop stale outbox assumptions from leaking into implementation and to lock the
minimum additive contract needed for meal insights.

## Key Insights

- The direct-Queue branch state is real, not proposed. `AsyncUnitOfWork` no
  longer exposes `outbox` or `affiliate_outbox`
  (`src/infra/database/uow_async.py:137-141`), and the deleted outbox paths
  from the older plan are already absent from the worktree.
- Durable meal cache invalidation already uses the Worker path:
  backend meal events (`src/app/events/meal/meal_events.py:10-27`)
  -> HTTP Queue publish (`src/infra/adapters/cloudflare_queue_publisher.py:93-137`)
  -> generic Worker routing
  (`../nutreeai_async/src/interfaces/cloudflare/integration-event-router.ts:52-149`)
  -> meal cache invalidation
  (`../nutreeai_async/src/application/event-handlers/meal-cache-invalidation-handler.ts:12-88`).
- Meal insight input is embedded in the generic meal event as `data.insight`.
  The Worker domain parser validates the bounded snapshot without coupling the
  interface layer to AI or cache implementations.
- The Worker-owned insight namespace is intentionally simple and shared by both
  repositories: `meal_insight:{meal_id}` with a seven-day TTL.

## Requirements

- Functional: declare the generic `IntegrationEvent` envelope authoritative for
  durable meal cache invalidation.
- Functional: declare the bounded `data.insight` snapshot authoritative for
  insight generation.
- Functional: keep the existing Worker payload shape authoritative; the Worker
  derives the cache key from `meal_id` and owns the seven-day TTL.
- Non-functional: preserve the dirty worktree; do not reintroduce the removed
  backend outbox stack.
- Non-functional: update stale active plans so implementation work does not
  follow deleted-file instructions.

## Architecture

```text
Meal cache invalidation:
meal mutation -> meal.created|updated|deleted.v1 -> Queue -> IntegrationEventRouter
-> MealCacheInvalidationHandler -> Redis delete operations

Meal insights:
post-write generic meal event with data.insight -> QueueConsumerRouter
-> cache handler + insight business handler -> AI generation -> cache write
(7d) -> optional FCM
```

Chosen contract rule:
- Use one generic envelope for cache invalidation and insights.
- Keep the insight snapshot nested under `data.insight`; the Worker derives the
  fixed key from `meal_id` and owns the seven-day TTL.

## Related Code Files

- Modify: `plans/260822-1730-cloudflare-async-cache-projection-worker/plan.md`
- Modify: `plans/260823-1056-generic-integration-event-redesign/plan.md`
- Modify: `plans/260824-1320-worker-first-meal-cache-and-insight-cutover/plan.md`
- Optional modify: `docs/decisions/260822-1631-cloudflare-async-cache-worker-proposal.md`
- Read-only contract sources:
  `src/infra/database/uow_async.py`,
  `src/domain/services/meal_value_insight_service.py`,
  `../nutreeai_async/src/domain/events/meal-value-insight-event.ts`,
  `../nutreeai_async/src/interfaces/cloudflare/integration-event-router.ts`,
  `../nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts`

## Implementation Steps

1. Mark `260822-1730-cloudflare-async-cache-projection-worker` as blocked by
   this plan so no one resumes its outbox-based rollout steps unchanged.
2. Mark `260823-1056-generic-integration-event-redesign` as blocking this plan
   because it owns the direct-Queue baseline already in the branch.
3. Record the authoritative fan-out: one generic meal event carries cache
   metadata and an optional bounded `data.insight` snapshot.
4. Lock the additive Worker contract for insights:
   derive `meal_insight:{meal_id}` in the Worker, use the fixed seven-day TTL,
   and include optional `tokens` only when the backend has them cheaply.
5. Record the exact caller inventory for Phases 2 and 3 so later work does not
   say "update all callers."
6. Remove or annotate stale plan/report references that still point at deleted
   outbox files or deleted tests.

## Todo List

- [x] Update overlapping plan dependencies
- [x] Lock one generic event with an optional insight snapshot
- [x] Lock Worker-owned insight cache namespace and TTL
- [x] Capture exact publisher/scheduler inventories
- [x] Annotate stale outbox-based instructions as branch-incompatible

## Success Criteria

- [x] Every active plan points at the direct-Queue branch reality, not the removed outbox path.
- [x] The new plan states one authoritative contract for cache invalidation and one for insights.
- [x] Worker cache behavior is explicitly listed and justified.
- [x] Exact caller inventories exist before code phases start.

## Risk Assessment

- High likelihood / High impact: a developer follows the older outbox plan and
  reintroduces deleted infrastructure.
  Mitigation: block the old plan and restate the branch authority here.
- Medium likelihood / High impact: the backend and Worker compute different
  insight cache keys.
  Mitigation: both sides use the fixed `meal_insight:{meal_id}` namespace;
  backend reader and Worker write tests assert the same key.
- Low likelihood / Medium impact: stale reports cause rollout confusion.
  Mitigation: annotate them as compatibility history, not implementation source.

## Security Considerations

- Do not add raw meal payloads, cache keys, emails, or tokens to plan prose or
  operational evidence.
- Keep additive event fields bounded and derived from existing safe data.

## Next Steps

Phase 2 assumes this contract freeze is done.

## Unresolved Questions

- None.
