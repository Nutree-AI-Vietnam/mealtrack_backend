---
title: "Local Outbox and Cache Scout"
type: research
status: complete
date: 2026-08-22
---

# Local Outbox and Cache Scout

## Summary

MealTrack already has the durable primitives needed for the first slice, but
they are not connected: `outbox_events` currently dispatches Python handlers,
while cache invalidation still schedules process-local tasks after the SQL
transaction. The safe design is additive: preserve existing outbox event
handlers and statuses, add destination/routing and lease fencing, then route a
new cache-projection event either to the local Redis handler or Cloudflare.

## Verified paths

- `src/infra/database/models/outbox_event.py:16-77` models the existing
  `outbox_events` table with `PENDING`, `IN_PROGRESS`, `COMPLETED`, and
  `FAILED_DEAD_LETTER` states.
- `src/infra/repositories/outbox_repository.py` claims with
  `FOR UPDATE SKIP LOCKED`, leases rows, and finalizes them. Finalization
  currently filters by row ID only; a fencing token is required before adding
  a second publisher/worker.
- `src/infra/services/outbox_dispatch_engine.py:38-182` already separates
  claim, external dispatch, and finalization into short database transactions.
- `src/infra/services/handlers/__init__.py:37-80` registers affiliate, push,
  telemetry, Firebase cleanup, and notification handlers. Cache projection
  dispatch must be registered without changing these existing routes.
- `src/infra/database/uow_async.py:96-146` exposes
  `AsyncUnitOfWork.outbox`; `__aexit__` commits the business and outbox rows
  together when the handler enqueues before leaving the context.
- `src/app/services/cache_invalidation_service.py:80-149` currently creates a
  coroutine and sends it to `BackgroundTaskManager`; lines 88-95 explicitly
  drop the job when no manager is configured.
- `src/infra/cache/cache_service.py:85-141` schedules cache population and
  already supports revision-aware writes through `revision_field`.
- `src/infra/cache/redis_client.py:160-188` implements the existing atomic
  Lua-based `set_if_revision_newer`; lines 199-227 implement bounded SCAN-based
  pattern deletion in Python.
- Meal writes invoke cache invalidation after commit. The call-site inventory
  is in `src/app/handlers/command_handlers/` plus meal-analysis and catalog
  flows; all `after_meal_write` callers must be classified before claiming
  transactional coverage.

## Design implications

1. Do not rename all existing statuses to `PUBLISHED`; `COMPLETED` is already
   asserted by the existing repository, worker, cleanup, fake, and test paths.
   For a queue destination, document `COMPLETED` as “destination accepted the
   message,” not “Redis finished.”
2. Add a lease token and require it in finalization. An old publisher must not
   mark a row completed after a second worker reclaimed its expired lease.
3. Enqueue the cache event through the same `AsyncUnitOfWork` before commit.
   Calling the existing post-commit service cannot prove the business write and
   cache job committed together.
4. Use one event containing exact operations. Keep cache-key construction in
   FastAPI; the Worker validates and executes operations but does not recreate
   MealTrack domain rules.
5. Add a monotonic nutrition projection revision before enabling asynchronous
   cache population fencing. Existing target revisions protect profile/target
   changes, not every meal-consumption mutation.

## Required regression coverage

- Existing affiliate/push/telemetry outbox handlers remain unchanged.
- Duplicate event IDs remain savepoint-safe.
- Expired lease reclaim cannot be finalized by the old lease owner.
- Every migrated meal write has the outbox insert inside its UoW transaction.
- A missing task manager no longer silently drops a migrated meal cache event.
- Existing optional-cache degradation remains: Redis failure does not fail the
  authoritative business write.

## Open gates

- Confirm every meal write call site and its UoW boundary before Phase 2.
- Confirm the deployed Redis provider supports an HTTP API and an atomic
  revision-fence operation plus bounded retryable deletion before enabling the
  Worker in production.
- Confirm the current outbox migration is applied in each target environment;
  local source presence is not deployment proof.
