---
phase: 4
title: "Worker Insight Cache And Push"
status: completed
priority: P1
effort: "2d"
dependencies: [1, 3]
---

# Phase 4: Worker Insight Cache And Push

## Overview

Move the Worker insight consumer into the application layer and upgrade it from "AI + optional FCM only" to
"cache-aware AI generation" so the backend read path can stop depending on
process-local background tasks.

## Key Insights

- The Worker generic router fans `meal.created.v1` and `meal.updated.v1` out to
  cache invalidation and the application-layer insight business handler.
- The Worker now has a narrow cache-write port and Redis `SET EX` support for
  insight persistence.
- The generic meal invalidation handler also deletes the matching
  `meal_insight:{meal_id}` key for UUID-backed meal events.
- The backend cache JSON shape is already stable and testable through
  `serialize_insights(...)`
  (`src/domain/services/meal_value_insight_contract.py:64-83`).
- FCM is already optional and best-effort in the Worker handler
  (`../nutreeai_async/src/infrastructure/event-handlers/meal-value-insight-event-handler.ts:50-69`).
  Cache write success must stay independent from FCM success.

## Requirements

- Functional: validate and normalize the generated insight payload before cache write.
- Functional: write the generated insight payload under `meal_insight:{meal_id}`
  for seven days before acknowledging the Queue message.
- Functional: keep FCM optional and best-effort. Push failure must not retry a
  successfully cached insight.
- Functional: preserve same-meal event ordering so mutation invalidation cannot
  delete a newly generated insight.
- Non-functional: keep legacy cache invalidation, email, and cleanup consumers untouched.
- Non-functional: preserve current retry semantics for parse errors, Redis
  failures, and AI failures.

## Architecture

```text
meal.created.v1 / meal.updated.v1 with data.insight
  -> parse embedded snapshot
  -> generate AI
  -> write backend-compatible JSON to Redis with TTL
  -> attempt optional FCM
  -> ack
```

## Related Code Files

- Modify: `../nutreeai_async/src/domain/events/meal-value-insight-event.ts`
- Modify: `../nutreeai_async/src/application/event-handlers/meal-cache-invalidation-handler.ts`
- Modify: `../nutreeai_async/src/domain/cache/cache-invalidation-builders.ts`
- Modify: `../nutreeai_async/src/domain/cache/cache-key-policy.ts`
- Modify: `../nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts`
- Modify: `../nutreeai_async/src/application/event-handlers/meal-insight-business-handler.ts`
- Modify: `../nutreeai_async/src/infrastructure/redis/redis-adapter.ts`
- Modify: `../nutreeai_async/src/domain/ports/cache-delete-port.ts` only if widened
- Create: `../nutreeai_async/src/domain/ports/cache-write-port.ts`
- Tests:
  `../nutreeai_async/test/meal-value-insight-event-handler.test.ts`,
  `../nutreeai_async/test/queue-consumer-router.test.ts`,
  `../nutreeai_async/test/fcm-adapter.test.ts`,
  and any new Redis adapter tests needed for set behavior

## Implementation Steps

1. Add the Worker cache-write interface backed by the existing `RedisAdapter`.
2. Update the insight handler to validate the generated result, serialize it
   into the backend JSON shape, write `meal_insight:{meal_id}` with the fixed
   seven-day TTL, and then attempt optional FCM.
3. Extend generic meal invalidation to delete the same insight key for valid
   meal UUIDs while preserving existing user-cache operations.
4. Keep message retry for parse, Redis, and AI failures; do not retry solely
   because push delivery failed after cache write.
5. Extend Worker tests for cache write, invalidation, FCM-best-effort failure,
   and retry on AI/Redis failures.

## Todo List

- [x] Add Worker cache-write primitive
- [x] Validate and normalize the generated payload
- [x] Write backend-compatible JSON with 7-day TTL
- [x] Keep FCM best-effort after cache write
- [x] Add atomic same-meal cache version fencing
- [x] Add focused Worker tests for cache write, push failure, generic fan-out,
  insight-neutral updates, ordering, and empty-result retry

## Success Criteria

- [x] Worker writes the same JSON shape the backend GET routes already parse.
- [x] Cache write success is independent from FCM success.
- [x] Meal updates and deletes clear the previous insight cache entry.
- [x] Existing non-insight Worker consumers retain their passing test coverage.

## Risk Assessment

- High likelihood / High impact: cache writes use a drifted JSON shape and the
  backend treats a stored entry as invalid.
  Mitigation: mirror `serialize_insights(...)` exactly and assert it in tests.
- Medium likelihood / Medium impact: duplicate events can still create duplicate
  AI calls under high concurrency.
  Mitigation: same-meal writes and invalidations use an atomic Redis version
  fence; duplicate-delivery suppression remains a follow-up if duplicate AI
  work becomes material.
- Low likelihood / High impact: widening the Redis adapter breaks existing cache
  invalidation consumers.
  Mitigation: keep delete methods unchanged and add new methods without altering
  existing call sites.

## Security Considerations

- Never log cache keys, raw tokens, or full prompt payloads.
- Keep the Worker-owned TTL fixed and reject malformed event data.
- Keep FCM credentials optional and scoped to the existing Worker environment vars.

## Next Steps

Phase 5 validates the full backend -> Queue -> Worker -> Redis -> GET read path.

## Unresolved Questions

- None.
