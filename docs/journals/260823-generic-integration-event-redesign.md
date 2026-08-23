# Generic Integration Event Redesign

**Date**: 2026-08-23 12:32 +07  
**Component**: MealTrack backend outbox + nutreeai_async Worker  
**Status**: MVP implemented and staging Worker deployed; business-event trace pending

## Decision

Use one generic `IntegrationEvent` for each business event. The backend writes
the event to the existing PostgreSQL transactional outbox in the same
transaction as the business mutation. The Worker consumes the single
environment-specific ingress queue and runs the registered handlers through one
in-process orchestrator.

For `hydration.created.v1`, the first handler translates the event to the
existing cache invalidation contract. Additional notification or email
handlers can be registered later without changing the backend event contract.

## Delivery semantics

- The event is ACKed only after every registered handler succeeds.
- A handler failure retries the whole ingress message and eventually sends it
  to the ingress DLQ.
- Earlier handlers can run again after a later handler fails, so handlers must
  be idempotent.
- There is no D1 delivery ledger, dynamic subscription catalog, child queue, or
  per-handler DLQ in the MVP.
- HMAC remains out of scope because staging and production use separate queue
  names and environment values.

## Cleanup

Hydration no longer publishes a second hydration-specific cache invalidation
outbox event. The generic hydration event owns that cache side effect. The
legacy `cache_invalidation.v1` path remains for other mutation paths that still
produce it directly.

The architecture cleanup also removed an orphaned Worker cache-consumer module,
kept the default hydration handler when future integration handlers are added,
and corrected the legacy meal-delete alias to use durable hydration cache
publication when an outbox is available. Its direct post-commit invalidation
remains only as a fallback for UoWs without a usable outbox event.

## Verification

- Backend focused event, hydration, and outbox tests pass.
- Worker typecheck and test suite pass, including multiple-handler ordering,
  whole-message retry, environment isolation, and default hydration routing.
- Wrangler staging dry-run and Worker deployment passed. A live business-event
  trace still needs a real hydration publication from the backend staging
  environment before end-to-end delivery is claimed.

Cloudflare cleanup completed: the unused delivery D1 databases and obsolete
staging queue/DLQ resources were deleted. Staging now has one ingress queue,
one DLQ, and the current Worker deployment attached to that queue.
