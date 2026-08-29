# Proposal: Cloudflare Async Cache and Projection Worker

**Status:** Proposed for team evaluation  
**Date:** 2026-08-22  
**Scope:** MealTrack asynchronous cache and derived-state processing  
**Decision owner:** Backend/platform team

## Overview

Move non-critical asynchronous cache and projection work out of the API
process and execute it on Cloudflare Workers using Cloudflare Queues.

The API remains responsible for business transactions and PostgreSQL remains
the source of truth. Cloudflare owns asynchronous delivery, retries, and
Redis cache maintenance.

This proposal does not move the FastAPI API, PostgreSQL, or business rules to
Cloudflare.

## Current problem

The current design already separates business work from cache maintenance:

```text
Actor
  -> Business API
       -> PostgreSQL: authoritative read/write state
       -> Redis: cache reads
       -> async cache job
            -> Redis: invalidation or projection write
```

However, cache jobs are still process-local. `CacheInvalidationService` and
`CacheService` schedule work through `BackgroundTaskManager`. If the API
process is restarted after the database commit, the cache job can be lost.

The repository already contains a transactional outbox for durable secondary
work. The proposal extends that pattern to cache invalidation and cache
projection jobs.

Relevant current implementation:

- `src/app/services/cache_invalidation_service.py`
- `src/infra/cache/cache_service.py`
- `src/infra/event_bus/background_task_manager.py`
- `src/cron/outbox_worker.py`
- `src/domain/ports/outbox_repository_port.py`
- `src/infra/database/models/outbox_event.py`

## Proposed architecture

```text
                         same database transaction
Business API  ───────────────┬──────────────────────────┐
                             │                          │
                             ▼                          ▼
                       PostgreSQL                  outbox_events
                       source of truth                    │
                                                          │ relay
                                                          ▼
                                                  Cloudflare Queue
                                                          │
                                                          ▼
                                                  Cloudflare Worker
                                                          │
                                                          ▼
                                                   Redis cache
```

### Business API responsibilities

- Complete the authoritative PostgreSQL write.
- Enqueue a typed outbox event in the same unit-of-work transaction.
- Return the business response without waiting for Redis.
- Continue reading Redis through the existing cache-aside path.
- Generate the canonical cache keys, patterns, and revision metadata.

### Cloudflare responsibilities

- Receive outbox messages through a Queue.
- Apply cache operations to Redis.
- Retry transient failures.
- Send permanently failing messages to a dead-letter queue.
- Acknowledge a message only after its Redis operation succeeds.
- Treat duplicate delivery as safe and idempotent.

Cloudflare Queues provide at-least-once delivery, so duplicate messages are
expected and must not change business correctness. Queue ordering must not be
assumed; revision-aware cache writes should reject older values.

## Proposed job contract

The API should send exact cache operations rather than making the Cloudflare
Worker duplicate MealTrack domain rules.

```json
{
  "schema_version": 1,
  "event_id": "cache-meal-user-123-2026-08-22",
  "event_type": "cache_projection",
  "aggregate_type": "meal",
  "aggregate_id": "meal-123",
  "operations": [
    {
      "kind": "delete_key",
      "key": "user:...:daily-macros:..."
    },
    {
      "kind": "delete_pattern",
      "pattern": "user:...:nutrition_bulk:*"
    }
  ],
  "occurred_at": "2026-08-22T09:31:00Z"
}
```

The payload should contain only internal identifiers, cache operations, and
revision metadata. It must not contain emails, access tokens, raw meal images,
or unnecessary nutrition payloads.

## Consistency model

Business data is strongly consistent in PostgreSQL. Cache data is eventually
consistent and disposable.

Expected behavior after a successful business write:

1. PostgreSQL commit succeeds.
2. The API returns successfully.
3. The cache event is eventually delivered.
4. Redis keys are invalidated or updated.
5. A later cache miss reconstructs data from PostgreSQL.

Cache invalidation is naturally idempotent. Cache population requires the
existing revision protection so an older asynchronous result cannot overwrite
a newer projection.

## Relay placement

For the first version, the existing Python outbox worker can act as the
publisher/relay:

```text
Python outbox worker
  -> publish event to Cloudflare Queue
  -> mark outbox row completed only after publish succeeds
```

This keeps the transactional and lease logic in the existing codebase while
Cloudflare hosts the actual asynchronous cache execution. Later, the relay
could move to a Cloudflare Cron Worker using a protected internal API or
Hyperdrive, but that would duplicate outbox-claiming logic and should not be
the first migration.

If publishing succeeds but marking the outbox row completed fails, the event
may be published twice. This is acceptable only because the consumer is
idempotent.

## Cloudflare product choice

### Recommended: Cloudflare Queues + Worker consumer

Queues fit this workload because cache work is discrete, retryable, and does
not need a permanently running process. Configure a dead-letter queue and
bounded consumer concurrency.

### Not recommended for the first version: Cloudflare Containers

Containers can run the existing Python image, but a continuously polling
background daemon adds lifecycle and operational complexity. Container sleep,
replacement, and restart behavior must be handled explicitly.

### Not recommended: Python Worker rewrite

Python Workers support FastAPI and common Python packages, but the runtime is
Pyodide/WebAssembly and the feature is still beta. Rewriting the current
SQLAlchemy, Redis, Firebase, and provider integrations for that runtime is
outside the value of this cache migration.

### Redis decision

Keep the existing Redis instance as the cache authority for the first phase.
The Cloudflare Worker will need a Worker-compatible Redis adapter, such as an
HTTP-based Redis client or a verified TCP-compatible client. Do not write to
Cloudflare KV while the API continues reading Redis; that would create two
different cache authorities.

## Initial migration scope

Start with cache invalidation for meal writes only.

Include:

- Manual meal creation and editing.
- Meal deletion.
- Meal-derived daily macro and weekly budget invalidation.
- Nutrition bulk and daily breakdown invalidation.

Defer until the first slice is proven:

- Hydration invalidation.
- Movement invalidation.
- Profile and target projections.
- Cache population after query misses.
- Notifications, Firebase cleanup, and AI workflows.

## Rollout plan

### Phase 1: Contract and ownership

- Define the versioned cache-projection event schema.
- Define event ownership so one event is processed by either the local runner
  or Cloudflare, never both by default.
- Add event IDs and revision metadata.
- Keep the local runner as an explicit fallback.

### Phase 2: Staging Queue

- Create staging queue and dead-letter queue.
- Implement the Worker consumer.
- Implement the Redis adapter.
- Add structured metrics for received, succeeded, retried, and dead-lettered
  jobs.
- Replay duplicate and out-of-order messages.

### Phase 3: Meal invalidation canary

- Route only meal invalidation events to Cloudflare.
- Restart the API immediately after a business commit and verify eventual
  cache maintenance.
- Simulate Redis failure and confirm Queue retry behavior.
- Confirm the business API still succeeds when Redis is unavailable.

### Phase 4: Expand and retire local work

- Add hydration and movement event types.
- Add profile and budget projection events where payloads are stable.
- Move cache population writes only after revision fencing is verified.
- Remove process-local scheduling from durable paths.

## Success criteria

- A committed business write always leaves a durable cache job.
- API restarts do not silently lose cache maintenance work.
- Duplicate Queue delivery does not corrupt or regress Redis state.
- Out-of-order projection writes cannot overwrite newer revisions.
- Redis failure does not fail the authoritative business request.
- Failed jobs are visible through retry and dead-letter metrics.
- No sensitive user data or provider credentials appear in Queue payloads or
  logs.
- PostgreSQL remains the only source of truth for meal, hydration, movement,
  profile, and nutrition state.

## Main risks

| Risk | Mitigation |
|---|---|
| Duplicate Queue delivery | Stable event IDs and idempotent Redis operations |
| Queue message published but outbox row not marked complete | Safe duplicate handling in the consumer |
| Older projection overwrites newer cache | Revision-aware writes |
| Redis client incompatibility in Workers | Small staging adapter proof before production |
| Cache key logic diverges between services | API creates exact keys and patterns |
| Jobs are processed twice during migration | Explicit event ownership and no default dual dispatch |
| Queue backlog hides user-visible staleness | Backlog, age, retry, and DLQ alerts |
| Cloudflare outage or misconfiguration | PostgreSQL remains authoritative; cache miss rebuilds from SQL |

## Team decision questions

1. Is Cloudflare Queue plus Worker consumer the preferred async runtime?
2. Should the first consumer write to the current Redis provider through HTTP,
   or should we validate a TCP-compatible client?
3. Is keeping the Python outbox relay temporarily acceptable?
4. Which cache event should be the first production canary: meal invalidation,
   affiliate webhook, or telemetry?
5. What queue backlog and dead-letter thresholds should page the team?
6. Do we want cache invalidation only in the first phase, or also cache
   population writes?

## References

- [MealTrack system architecture](../system-architecture.md)
- [MealTrack CQRS guide](../cqrs-guide.md)
- [Transactional outbox worker](../../src/cron/outbox_worker.py)
- [Cache invalidation service](../../src/app/services/cache_invalidation_service.py)
- [Cache service](../../src/infra/cache/cache_service.py)
- [Cloudflare Queues](https://developers.cloudflare.com/queues/)
- [Cloudflare Queue delivery guarantees](https://developers.cloudflare.com/queues/reference/delivery-guarantees/)
- [Cloudflare Queue retries and dead-letter queues](https://developers.cloudflare.com/queues/configuration/batching-retries/)
- [Cloudflare Worker external services](https://developers.cloudflare.com/workers/configuration/integrations/external-services/)
- [Cloudflare Hyperdrive](https://developers.cloudflare.com/hyperdrive/)
