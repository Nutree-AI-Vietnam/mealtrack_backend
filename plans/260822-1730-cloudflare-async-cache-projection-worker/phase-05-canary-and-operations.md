---
phase: 5
title: "Staging and Production Enablement"
status: pending
priority: P1
effort: "1d"
dependencies: [4]
---

# Phase 5: Staging and Production Enablement

## Context links

- Phases 1-4 tests and provider evidence
- `docs/system-architecture.md`
- `docs/external-services.md`
- `docs/runbooks/`
- `.github/workflows/` deployment conventions
- Worker observability:
  https://developers.cloudflare.com/workers/observability/

## Overview

Verify the complete path in staging, then enable one global production Queue
publisher. There is no percentage canary or local fallback route. Rollback
means disabling new Queue publication or reverting the Worker; business data
remains correct and pending events remain retryable.
This phase is blocked on staging/live credentials and provider proof.

## Requirements

- Separate staging and production Queue/DLQ resources, Worker environments,
  Redis namespaces, and secrets.
- One global enablement setting controls whether new cache events are published
  to Queue. No per-user routing or dual local/Cloudflare dispatch.
- Alerts cover outbox age, Queue publish failures, Worker retries, Redis errors,
  and DLQ arrivals.
- Runbook documents staging verification, enablement, disablement, Queue drain,
  DLQ inspection, and read-through cache rebuild behavior.
- Source, CI, deployment, and live Queue/Worker/Redis evidence are reported
  separately.

## Related code files

### Create

- `.github/workflows/deploy-cache-invalidation-worker.yml` if repository CI owns
  Worker deployment
- `docs/runbooks/cache-invalidation-queue.md`
- `plans/260822-1730-cloudflare-async-cache-projection-worker/reports/phase-05-rollout-evidence.md`

### Modify

- `docs/system-architecture.md`
- `docs/external-services.md`
- `docs/troubleshooting.md` or the relevant runbook index
- `.env.example` and deployment secret documentation without real values

## Implementation steps

1. Create staging Queue, DLQ, Worker environment, Redis namespace, and runtime
   secrets.
2. Deploy the Worker and run valid, duplicate, malformed, Redis-down, and
   bounded-pattern tests.
3. Enable staging publication for all approved meal events. Restart the API
   after commit and verify the outbox event survives process loss.
4. Exercise business-write success while Queue/Redis is unavailable. Confirm
   PostgreSQL success, outbox retry/age metrics, and later cache invalidation.
5. Enable the single production Queue setting after staging evidence is
   complete.
6. Exercise rollback by disabling new publication or reverting the Worker;
   record pending-event age and cache rebuild behavior.
7. Update architecture, external-service, troubleshooting, and runbook docs
   only after evidence is attached.

## Todo list

- [ ] Deploy staging Queue/DLQ/Worker and secrets.
- [ ] Run failure/restart/duplicate tests.
- [ ] Capture backend, Queue, Worker, Redis, and DLQ evidence.
- [ ] Enable the single production path.
- [ ] Exercise disable/revert rollback.
- [ ] Sync evergreen docs.

## Success criteria

- [ ] API business writes remain successful when Queue or Redis is unavailable.
- [ ] Restart after commit does not lose a migrated cache event.
- [ ] Worker retries and DLQ are visible and actionable.
- [ ] Duplicate events do not create incorrect cache state.
- [ ] Staging and production evidence include timestamps and revision IDs.

## Next steps

Future work may add cache-population revision fencing only if stronger freshness
guarantees become necessary. It is not part of this invalidation slice.
