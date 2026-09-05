# Red-Team Review: Failure Mode Analyst

## Finding 1: Queue acceptance loses PostgreSQL completion correlation — HIGH

- **Location:** Phase 3 relay and Phase 5 rollback/DLQ
- **Flaw:** The plan completes the PostgreSQL row after Queue acceptance, while
  later Worker failure is tracked only by Cloudflare. There is no explicit
  replay/remediation contract that binds a DLQ message back to the outbox row.
- **Failure scenario:** Queue accepts event E, API marks it completed, Worker
  retries E to DLQ, and the operator cannot tell whether E was already applied,
  whether it is safe to replay, or which business mutation produced it.
- **Evidence:** Current PostgreSQL terminal state is
  `COMPLETED`/`FAILED_DEAD_LETTER` in `src/domain/models/outbox_status.py:14-22`,
  while the current cleanup removes completed rows in
  `src/infra/repositories/outbox_repository.py`.
- **Suggested fix:** Require the envelope's event ID in every Worker/DLQ log,
  define a bounded DLQ inspection/replay procedure, retain completed cache rows
  long enough for Queue retention, and make replay explicitly idempotent.

## Finding 2: Pattern deletion is not one atomic Redis operation — HIGH

- **Location:** Phase 4 atomic fence plus operation executor
- **Flaw:** The plan uses “atomic fence + operation” language for pattern deletes,
  but current pattern deletion is a SCAN loop with batched deletes. A whole
  pattern cannot be atomically scanned and deleted without a provider-specific
  script/namespace design.
- **Failure scenario:** Worker advances the fence, scans part of a pattern,
  times out, and retries. Some keys remain stale until retry; if the fence is
  not advanced first, a concurrent old population can resurrect a key.
- **Evidence:** Current implementation is explicitly iterative at
  `src/infra/cache/redis_client.py:199-225`; it is not a single atomic command.
- **Suggested fix:** Define atomicity precisely: atomically compare-and-advance
  the fence first, then perform bounded idempotent deletes. Treat partial
  deletion as retryable, and test the fence guarantee separately from deletion
  completeness.

## Finding 3: Rollback can strand already-published Queue events — HIGH

- **Location:** Phase 5 rollback
- **Flaw:** Switching new events to `local_redis` does not process old messages
  already accepted by Cloudflare. Pausing the Worker or abandoning the Queue
  can leave cache invalidation stale until retention expires.
- **Failure scenario:** Production canary is rolled back after a Worker outage;
  old messages remain in the Queue/DLQ while new events use local delivery. The
  API is healthy, but canary users retain stale caches.
- **Evidence:** Current cache is optional/read-through, so misses rebuild from
  SQL (`docs/external-services.md:23-39`), but current invalidation is
  process-local and drop-prone (`src/app/services/cache_invalidation_service.py:80-100`).
- **Suggested fix:** Keep the Worker consumer running during rollback to drain
  old messages, or provide an explicit pull/replay tool that processes the same
  signed envelope locally. Record queue age and drain completion as rollback
  gates.

