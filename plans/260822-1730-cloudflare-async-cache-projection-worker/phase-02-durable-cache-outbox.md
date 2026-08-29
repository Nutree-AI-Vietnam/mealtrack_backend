---
phase: 2
title: "Transactional Outbox Integration"
status: completed
priority: P1
effort: "1-2d"
dependencies: [1]
---

# Phase 2: Transactional Outbox Integration

## Context links

- Phase 1 contract and caller matrix
- `src/infra/database/uow_async.py`
- `src/infra/repositories/outbox_repository.py`
- `src/infra/database/models/outbox_event.py`
- `src/app/services/cache_invalidation_service.py`
- `src/domain/cache/cache_keys.py`

## Overview

Replace the migrated meal paths' process-local post-commit invalidation with an
outbox insert in the same `AsyncUnitOfWork` transaction. Reuse the existing
outbox schema, statuses, claim, lease, and retry behavior. Do not add a
projection-revision table, fencing fields, or a second local delivery path.
This slice is implemented in the working tree.

## Requirements

- A migrated meal write and its `cache_invalidation.v1` event commit together
  or roll back together.
- The event contains only exact cache operations derived from the existing
  canonical key builder; it contains no meal or nutrition snapshot.
- Existing outbox event types and public API responses remain compatible.
- A missing `BackgroundTaskManager` cannot drop a migrated event.
- Existing cache population and read-through behavior remains unchanged; the
  accepted consistency model is eventual invalidation.
- Idempotent business replay branches do not enqueue duplicate mutation events
  unless the current code's mutation was actually committed again.

## Related code files

### Create

- `src/domain/cache/cache_invalidation_operations.py`
- `tests/unit/domain/cache/test_cache_invalidation_operations.py`
- `tests/unit/app/services/test_cache_invalidation_service.py`

### Modify

- `src/app/services/cache_invalidation_service.py`
- All approved meal-write handlers from the Phase 1 caller matrix
- `src/infra/database/uow_async.py` only if the existing outbox seam needs a
  narrow typed helper
- Existing outbox and transaction tests

## Implementation steps

1. Write characterization tests for current invalidation key coverage and
   existing outbox transaction behavior.
2. Extract a pure operation builder and preserve current exact-key and pattern
   coverage, including backdated-week behavior.
3. Add a narrow UoW/outbox helper for `cache_invalidation.v1` events.
4. Migrate every approved meal-write caller to incrementally build and enqueue
   the event before UoW exit. Fail the checklist if any live caller remains
   post-commit and unclassified.
5. Remove the migrated callers' `BackgroundTaskManager` dependency while
   preserving unrelated profile, hydration, movement, and existing cache paths.
6. Verify rollback removes the event with the business transaction and that
   optional-cache degradation remains intact.

## Todo list

- [x] Add pure invalidation operation builder.
- [x] Add transactional cache event helper and tests.
- [x] Migrate all approved meal-write callers.
- [x] Remove post-commit task scheduling for migrated paths.
- [x] Verify rollback and existing outbox regression coverage.

## Success criteria

- [x] Business row and outbox event commit atomically in tests.
- [x] Every approved meal-write caller is covered by the caller matrix.
- [x] Current invalidation key coverage is preserved.
- [x] No revision table, fencing, cache-population, or dual-routing code exists.

## Next steps

Phase 3 publishes pending cache events to Cloudflare Queue through the existing
Python outbox worker.
