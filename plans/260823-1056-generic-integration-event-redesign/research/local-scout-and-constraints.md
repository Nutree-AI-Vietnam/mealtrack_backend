# Local Scout and Constraints

## Scope

Cross-repository communication from the MealTrack FastAPI backend to the
standalone `nutreeai_async` Cloudflare Worker. This plan changes the durable
integration-event boundary; it does not replace the internal PyMediator event
bus.

## Verified stack and ownership

- MealTrack: Python 3.13, FastAPI, async SQLAlchemy, PostgreSQL/Neon,
  PyMediator, Redis, and Cloudflare Queue HTTP publishing.
- `nutreeai_async`: TypeScript Cloudflare Worker with Queue consumers,
  scheduled triggers, and external I/O adapters.
- MealTrack owns authoritative business transactions and the transactional
  outbox. The Worker owns asynchronous consumption, external side effects,
  ACK/retry, and DLQ behavior.

## Existing reusable paths

- `src/domain/events/base.py` defines internal `Event`, `Command`, `Query`,
  `DomainEvent`, and handler abstractions.
- `src/infra/database/models/outbox_event.py` stores one durable event payload
  with a unique `event_id`, status, lease, retry, and dead-letter fields.
- `src/infra/repositories/outbox_repository.py` inserts events inside the
  caller transaction and claims due rows with leases.
- `src/infra/services/outbox_dispatch_engine.py` dispatches claimed rows outside
  the transaction, then records completion/retry/dead-letter outcomes.
- `src/infra/adapters/cloudflare_queue_publisher.py` publishes JSON payloads to
  Cloudflare Queue and classifies retryable versus permanent failures.
- `nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts` already
  validates event types, invokes handlers, and owns per-message ACK/retry.
- `nutreeai_async/wrangler.jsonc` already defines separate queues and DLQs for
  cache invalidation, notifications, insights, and cleanup.

## Current constraints

- Existing `DomainEvent` objects are in-process Python events and must not
  become the cross-language wire contract directly.
- Queue delivery is at-least-once. A redesign must use deterministic delivery
  identity and idempotent handlers; exactly-once external effects are not
  available from Queue ACKs alone.
- A single message handled by multiple in-process handlers cannot provide
  independent retries. Independent delivery requires separate handler delivery
  messages or durable per-handler state.
- Current `cache_invalidation.v1` carries explicit bounded Redis operations.
  Generic business events should not force the Worker to duplicate backend
  cache-key derivation during the first migration.
- Event payloads must stay bounded and must exclude secrets, auth material,
  raw provider payloads, and full ORM/database entities.

## Current event boundary evidence

- Backend cache flow: `docs/runbooks/cache-invalidation-queue.md:3-5`.
- Backend event envelope construction:
  `src/app/services/cache_invalidation_service.py:73-95`.
- Backend outbox durability:
  `src/infra/database/models/outbox_event.py:23-63`.
- Worker cache validation and ACK/retry:
  `../nutreeai_async/src/interfaces/cloudflare/queue-consumer-router.ts:217-255`.
- Worker queue topology:
  `../nutreeai_async/wrangler.jsonc:19-48`.

## Related active plan

`plans/260822-1730-cloudflare-async-cache-projection-worker/` contains the
existing cache-invalidation implementation and external deployment gate. This
plan reuses its outbox/publisher/Worker work through compatibility adapters; it
does not require staging credentials before contract and local migration work.

