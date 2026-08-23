---
phase: 1
title: "Contract and ownership"
status: completed
priority: P2
effort: "0.5-1 day"
dependencies: []
---

# Phase 1: Contract and ownership

## Overview

Keep the cross-repository contract small and make hydration event ownership
explicit. The event crosses the queue as JSON; typed handler interfaces exist
only in `nutreeai_async`.

## Requirements

- Keep the versioned `IntegrationEvent` envelope and `hydration.created.v1`.
- Keep stable IDs, timestamps, environment, aggregate identity, and minimal data.
- Define a typed Worker handler contract and a code-owned event-to-handlers registry.
- Accept event-level retry/DLQ coupling for the MVP.
- Make the generic hydration cache handler the sole cache owner for hydration writes.
- Keep email/notification routes inactive until their recipient and effect-idempotency contracts are explicit.

## Architecture

```text
HydrationCreatedEvent
  -> route registry
      -> CacheInvalidationHydrationHandler
      -> future NotificationHydrationHandler
      -> future EmailHydrationHandler
```

The registry maps event type to typed handlers. It is static code for the MVP;
adding a handler means adding a typed handler and one registry entry. No runtime
subscription service or handler queue is needed.

## Related Code Files

- Modify: `src/app/events/integration_event.py`
- Modify: `nutreeai_async/src/domain/events/integration-event.ts`
- Modify: `nutreeai_async/src/interfaces/cloudflare/integration-event-router.ts`
- Tests: existing backend and Worker integration-event tests

## Implementation Steps

1. Confirm the hydration payload is immutable and contains no tokens, secrets, or provider-specific fields.
2. Define `IntegrationEventHandler<TEvent>` and the event-to-handlers registry in the Worker.
3. Register `CacheInvalidationHydrationHandler`; keep future provider handlers inactive.
4. Document at-least-once delivery and handler-level idempotency requirements.
5. Record the cache ownership decision so the backend does not publish two hydration cache effects.

## Success Criteria

- [x] Python and TypeScript validators accept the same hydration fixture.
- [x] The Worker route registry can represent multiple typed handlers for one event.
- [x] No D1 delivery state or dynamic subscription configuration appears in the MVP contract.
- [x] Cache ownership is unambiguous.

## Risk Assessment

External handlers may duplicate effects after a queue retry. Keep provider
handlers inactive until each effect has an idempotency strategy; cache
invalidation is the safe first live handler because repeated deletion is harmless.
