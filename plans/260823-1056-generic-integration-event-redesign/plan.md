---
title: "Orchestrated Hydration Integration MVP"
description: "Simplify backend-to-Worker integration events around one hydration event and one Worker orchestrator that invokes typed handlers."
status: in-progress
priority: P2
branch: "architecture/optimize-architecture"
tags: [architecture, integration-events, hydration, cloudflare-queues]
blockedBy: []
blocks: []
created: "2026-08-23T03:57:04.151Z"
createdBy: ck:plan
source: skill
---

# Orchestrated Hydration Integration MVP

## Overview

Use one versioned `hydration.created.v1` event as the stable boundary between
`mealtrack_backend` and `nutreeai_async`. The backend writes the event to the
existing PostgreSQL outbox in the same transaction as the hydration. The Worker
passes it to a code-owned handler registry. The orchestrator invokes each
handler for the event and ACKs only when all handlers succeed. Cloudflare Queue
retries the whole event when any handler fails and moves it to the ingress DLQ
after the configured retry limit.

The MVP deliberately does not add a delivery database, lease ledger, runtime
subscription service, or exactly-once claim. Queue delivery is at-least-once;
handlers own effect idempotency.

## Core flow

```text
hydration write + outbox
  -> mealtrack-events[-environment]
  -> event orchestrator
      -> CacheInvalidationHydrationHandler
      -> future NotificationHydrationHandler
      -> future EmailHydrationHandler
  -> ACK or ingress DLQ
```

## Phases

| Phase | Name | Status | Result |
|---|---|---|---|
| 1 | [Contract and ownership](./phase-01-contract-and-ownership.md) | completed | Minimal event and handler contracts |
| 2 | [Backend outbox and duplicate cleanup](./phase-02-backend-outbox-and-duplicate-cleanup.md) | completed | One canonical hydration publication |
| 3 | [Worker event orchestrator](./phase-03-worker-event-orchestrator.md) | completed | Typed handlers and event-level retry/DLQ |
| 4 | [Staging verification and decommission](./phase-04-staging-verification-and-decommission.md) | in-progress | Live MVP proof and redundant-path cleanup |

## Scope decisions

- Keep the existing PostgreSQL outbox and backend ownership of business data.
- Keep one ingress queue per environment.
- Use one Worker orchestrator and a code-owned handler registry for the MVP; do not add dynamic JSON subscription configuration.
- Make hydration cache invalidation the first live generic handler.
- Keep notification and email handler contracts extensible but do not activate provider side effects until their recipient/enrichment and idempotency contracts are explicit.
- Make `hydration.created.v1` the cache owner for hydration writes; remove the duplicate hydration-specific cache event publication.
- The Worker uses one physical queue and one DLQ per environment. Legacy event payloads may still route by event type during migration, but they do not get separate queue bindings.
- The unused remote D1 delivery databases and obsolete staging queue resources were removed after the Worker runtime no longer referenced them.

## Success criteria

- One committed hydration creates one canonical integration outbox event.
- The event reaches the ingress queue only through the outbox dispatcher.
- The Worker invokes all configured typed handlers without a D1 delivery ledger.
- A handler failure retries the whole event and reaches the ingress DLQ.
- Previously successful handlers may run again after retry; cache invalidation is safe to repeat.
- Independent handler retry/DLQ is explicitly deferred until a later fan-out phase.
- Staging proves the complete path before production configuration is enabled.

## Dependencies

- Existing PostgreSQL outbox, dispatcher, and Cloudflare publisher.
- Existing Redis cache invalidation handler and queue infrastructure.
- `nutreeai_async` Worker queue bindings and environment-specific credentials.
- Related cache projection work remains a compatibility dependency, not a reason to duplicate cache publication.
