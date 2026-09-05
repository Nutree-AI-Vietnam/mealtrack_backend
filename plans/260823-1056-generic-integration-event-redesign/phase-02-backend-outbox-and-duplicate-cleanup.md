---
phase: 2
title: "Backend outbox and duplicate cleanup"
status: completed
priority: P1
effort: "1-2 days"
dependencies: [1]
---

# Phase 2: Backend outbox and duplicate cleanup

## Overview

Keep the existing PostgreSQL transactional outbox as the only durable
publication mechanism. Publish one canonical hydration event and remove the
hydration-specific duplicate cache publication.

## Requirements

- Hydration rows and `hydration.created.v1` commit in the same unit of work.
- Queue availability must not determine whether the event is persisted.
- The outbox dispatcher publishes only to the explicit environment ingress queue.
- Existing specialized events remain available for non-hydration operations.
- Hydration cache invalidation is not emitted through both old and new paths.

## Related Code Files

- Modify: `src/app/handlers/command_handlers/log_hydration_command_handler.py`
- Modify: `src/app/services/cache_invalidation_service.py` only if needed to remove the hydration enqueue call
- Modify: `src/infra/services/handlers/__init__.py`
- Modify: `src/infra/config/settings.py`
- Modify: `src/infra/adapters/cloudflare_queue_publisher.py`
- Tests: hydration command, outbox repository, dispatcher, and publisher tests

## Implementation Steps

1. Preserve the existing `HydrationCreatedEvent` outbox enqueue inside the hydration transaction.
2. Remove the direct hydration-specific `cache_invalidation.v1` enqueue when the generic cache handler becomes active.
3. Keep legacy cache invalidation publication for non-hydration write paths.
4. Use one environment-specific `CLOUDFLARE_QUEUE_NAME` for generic and compatibility event publication.
5. Update tests to prove one hydration operation produces one canonical event and one cache effect path.

## Success Criteria

- [x] A successful hydration commit creates one canonical event and outbox row.
- [x] A rolled-back transaction creates no publishable hydration event.
- [x] Queue-disabled mode leaves the event pending in PostgreSQL.
- [x] Hydration cache invalidation has one owner and no duplicate enqueue.
- [x] Existing legacy event routes remain green.

## Risk Assessment

Removing the old cache event too early could leave caches stale. Keep the generic
cache route disabled until the Worker staging trace succeeds; then remove the
duplicate backend path in the same controlled change.
