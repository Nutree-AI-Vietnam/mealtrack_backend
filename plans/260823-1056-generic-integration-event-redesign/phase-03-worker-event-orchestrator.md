---
phase: 3
title: "Worker event orchestrator"
status: completed
priority: P1
effort: "1-2 days"
dependencies: [1, 2]
---

# Phase 3: Worker event orchestrator

## Overview

Keep one ingress Queue and add one small orchestrator in `nutreeai_async`. It
parses the event, looks up typed handlers, invokes them in order, and ACKs only
after all handlers succeed.

## Requirements

- Keep one ingress Queue per environment and one ingress DLQ.
- Define a code-owned map from event type to typed handlers.
- Register `CacheInvalidationHydrationHandler` for `hydration.created.v1`.
- Allow future notification/email handlers to subscribe by adding a registry entry.
- Retry the whole event when any handler throws; rely on the ingress Queue DLQ.
- Keep handlers idempotent because earlier handlers may execute again after retry.
- Preserve legacy direct consumers for unrelated event types.
- Validate the common envelope once and let handlers retrieve their own data by
  aggregate ID.

## Architecture

```text
mealtrack-events-staging
  -> IntegrationEventOrchestrator
      -> CacheInvalidationHydrationHandler
      -> ACK
```

The local test registry includes multiple handlers to prove that one event can
invoke many handlers. Production starts with the safe cache handler only;
provider side effects are not silently invented for the MVP.

## Related Code Files

- Modify: `nutreeai_async/src/interfaces/cloudflare/integration-event-router.ts`
- Modify: `nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts`
- Modify: `nutreeai_async/src/index.ts`
- Modify: `nutreeai_async/wrangler.jsonc`
- Delete: `nutreeai_async/src/infrastructure/delivery/delivery-state-store.ts`
- Delete: `nutreeai_async/migrations/0001_handler_deliveries.sql`
- Delete: `nutreeai_async/src/domain/events/integration-delivery.ts` if no longer needed by the orchestrator
- Tests: `nutreeai_async/test/integration-event-router.test.ts` and queue-router tests

## Implementation Steps

1. Remove D1 imports, bindings, migrations, claim/lease writes, and delivery-state options.
2. Remove dynamic `INTEGRATION_SUBSCRIPTIONS` parsing.
3. Replace it with a code-owned event-to-handler registry and generic envelope parser.
4. Implement `CacheInvalidationHydrationHandler` by looking up hydration context
   from Neon and adapting the existing cache handler.
5. Make the orchestrator ACK after all handlers succeed and retry on any handler failure.
6. Add tests for multiple handlers, ordering, failure retry, repeated cache invalidation, and unknown events.

## Success Criteria

- [x] One hydration event invokes every handler registered for its event type.
- [x] A handler failure retries the parent event and eventually reaches the ingress DLQ.
- [x] No Worker code imports D1 or maintains a delivery ledger.
- [x] Existing cache, notification, insight, and cleanup consumers remain compatible.
- [x] Adding a new integration event does not require a new Worker parser.

## Risk Assessment

This MVP intentionally couples handler retry state. If a later handler fails,
earlier handlers run again. This is acceptable for idempotent cache invalidation;
independent handler queues can be added later without changing the event contract.
