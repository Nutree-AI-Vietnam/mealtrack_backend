# Cloudflare Queue Cache Invalidation Progress

Status: local implementation done, rollout blocked

## Evidence

- Transactional cache invalidation, outbox integration, publisher wiring, and
  Worker consumer code are present in the working tree.
- Primary source paths:
  - `src/app/services/cache_invalidation_service.py`
  - `src/infra/adapters/cloudflare_queue_publisher.py`
  - `src/infra/services/handlers/cache_invalidation_queue_handler.py`
  - sibling `nutreeai_async` repository
- Test coverage for the contract, outbox, publisher, Worker, and handler paths
  is present in the working tree.

## Current status

- Phase 1: in progress, because Redis provider HTTP proof is still blocked on
  credentials / external access.
- Phase 2: completed.
- Phase 3: local publisher done; staging publish blocked on credentials.
- Phase 4: local Worker done; staging Queue/DLQ blocked on credentials.
- Phase 5: blocked on staging/live credentials and deployment access.

## Exclusions kept out

- HMAC signing
- Projection revision table / fencing
- Cache-population changes
- Local-vs-Cloudflare dual routing
- Percentage canary logic

## Next actions

1. Get provider credentials / environment access for exact-delete and bounded
   pattern-delete proof.
2. Capture staging/live Queue, Worker, and Redis evidence.
3. Keep the rollout blocked until live proof is attached.

## Unresolved questions

- Which Redis provider account and namespace own the live proof target?
- Who owns staging/live Queue and Worker deployment access?
- Which Queue names and secrets are the final rollout set?
